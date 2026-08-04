#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SVG / PDF / PNG 等 一键换色 · 本地网页版

架构说明：上传和换色都是异步任务（后台线程），接口立即返回，
前端轮询 /api/status/<id> 获取结果，大文件处理时界面不会卡住。
"""

import base64
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from contextlib import redirect_stderr
from pathlib import Path

import fitz
from flask import Flask, abort, jsonify, render_template, request, send_file

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent))
import recolor  # noqa: E402

SESSIONS = Path(os.environ.get("SESSIONS_DIR", str(BASE / "sessions")))
SESSIONS.mkdir(exist_ok=True)
RASTER_EXTS = recolor.RASTER_EXTS
ALLOWED = {".pdf", ".svg"} | RASTER_EXTS
SID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024
HISTORY_DAYS = int(os.environ.get("HISTORY_DAYS", "7"))
_active_jobs = set()
_active_lock = threading.Lock()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD


# ---------------------------------------------------------------- 会话与异步任务

def _session_dir(sid):
    return SESSIONS / sid


def _read_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def _meta(sid):
    return _read_json(_session_dir(sid) / "meta.json", {}) or {}


def _set_meta(sid, **kw):
    d = _session_dir(sid)
    m = _meta(sid)
    m.update(kw)
    (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


def _set_progress(sid, percent, message):
    try:
        (_session_dir(sid) / "progress.json").write_text(
            json.dumps({"percent": percent, "message": message}), encoding="utf-8"
        )
    except Exception:
        pass


def _start_job(sid, fn):
    def job():
        with _active_lock:
            _active_jobs.add(sid)
        try:
            fn()
        except Exception as e:
            try:
                _set_meta(sid, status="error", error=str(e))
            except Exception:
                pass
        finally:
            with _active_lock:
                _active_jobs.discard(sid)

    threading.Thread(target=job, daemon=True).start()


def _cleanup_sessions(days=HISTORY_DAYS):
    """删除超过 N 天的历史会话目录。"""
    cutoff = time.time() - days * 86400
    for d in SESSIONS.iterdir():
        if not d.is_dir():
            continue
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d)
        except Exception:
            pass


# ---------------------------------------------------------------- 扫描 / 预览

def _sorted_color_items(colors, kind=None):
    items = sorted(colors.items(), key=lambda kv: -kv[1]["count"])
    out = []
    for rgb, info in items:
        fc = info.get("fill_count", 0)
        sc = info.get("stroke_count", 0)
        item_kind = kind
        if item_kind is None:
            item_kind = "fill+stroke" if fc and sc else ("stroke" if sc else "fill")
        out.append(
            {
            "hex": recolor.rgb_to_hex(rgb),
            "rgb": list(rgb),
            "count": info["count"],
            "opacities": sorted(info["opacities"]),
                "fill_count": fc,
                "stroke_count": sc,
                "kind": item_kind,
            }
        )
    return out


def scan_colors(path, ext, on_progress=None):
    if ext == ".svg":
        colors = recolor.scan_svg_colors(path.read_text(encoding="utf-8", errors="replace"))
        return _sorted_color_items(colors), len(colors)
    if ext == ".pdf":
        colors = recolor.scan_pdf_colors(str(path), on_progress)
        return _sorted_color_items(colors), len(colors)
    colors, total = recolor.scan_raster_colors(str(path), limit=800)
    return _sorted_color_items(colors, kind="pixel"), total


def render_preview(path, max_w=1000):
    """把文件渲染成 PNG 的 data URL（PDF/SVG 用 fitz，位图直接用 PIL，避免解码损耗）。"""
    ext = Path(path).suffix.lower()
    if ext in RASTER_EXTS:
        from PIL import Image

        im = Image.open(path).convert("RGBA")
        if im.width > max_w:
            h = max(1, int(im.height * max_w / im.width))
            im = im.resize((max_w, h), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    doc = fitz.open(path)
    try:
        page = doc[0]
        zoom = min(2.0, max_w / max(page.rect.width, 1.0))
        pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png = pm.tobytes("png")
    finally:
        doc.close()
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _preview_background(preview_data_url):
    """预览图中占比最大的颜色，视为背景色。"""
    import numpy as np
    from PIL import Image

    raw = base64.b64decode(preview_data_url.split(",", 1)[1])
    arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
    packed = (
        arr[..., 0].astype(np.uint32) << 16
        | arr[..., 1].astype(np.uint32) << 8
        | arr[..., 2].astype(np.uint32)
    )
    vals, counts = np.unique(packed, return_counts=True)
    p = int(vals[np.argmax(counts)])
    return "#%02x%02x%02x" % ((p >> 16) & 255, (p >> 8) & 255, p & 255)


def _finish_upload(sid, src, ext):
    d = _session_dir(sid)
    _set_progress(sid, 5, "读取文件")
    if ext == ".pdf":
        def cb(done, total):
            pct = 8 + int(done / max(total, 1) * 78)
            _set_progress(sid, pct, f"扫描内容流 {done}/{total}")
        items, total = scan_colors(src, ext, cb)
    else:
        _set_progress(sid, 30, "统计颜色")
        items, total = scan_colors(src, ext)
    _set_progress(sid, 92, "生成预览")
    result = {"colors": items, "total": total, "preview": render_preview(src)}
    result["background"] = _preview_background(result["preview"])
    (d / "scan.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    _set_meta(sid, status="ready")
    _set_progress(sid, 100, "完成")


def _map_preview(preview_data_url, table, tolerance, smooth, anchors):
    """对已有预览做像素级换色映射，快速生成“换色后”预览。"""
    import numpy as np
    from PIL import Image

    raw = base64.b64decode(preview_data_url.split(",", 1)[1])
    arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
    stats = {"matched": 0}
    new_rgb = recolor.map_rgb_array(arr, table, tolerance, anchors if smooth else None, stats)
    buf = io.BytesIO()
    Image.fromarray(new_rgb).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), stats["matched"]


def _pdf_shading_warning(path):
    """快速检查页面资源里是否有 Shading(渐变)，用于预览期提示（不解压内容流）。"""
    doc = fitz.open(path)
    warns = []
    try:
        for i, page in enumerate(doc, 1):
            try:
                kind, val = doc.xref_get_key(page.xref, "Resources/Shading")
                if kind in ("dict", "xref") and val not in ("", "null"):
                    warns.append(f"第 {i} 页含渐变/着色对象(shading)，其内部颜色未替换")
            except Exception:
                pass
    finally:
        doc.close()
    return "\n".join(warns)


def _finish_apply_pdf_preview(sid, src, table, tolerance, smooth):
    """PDF 快路径：只生成预览，真正的矢量换色+压缩在下载时才执行。"""
    d = _session_dir(sid)
    scan = _read_json(d / "scan.json") or {}
    anchors = [tuple(c["rgb"]) for c in (scan.get("colors") or [])][:10] if smooth else None
    preview_src = scan.get("preview") or render_preview(src)
    _set_progress(sid, 10, "生成预览")
    preview, matched = _map_preview(preview_src, table, tolerance, smooth, anchors)
    (d / "mapping.json").write_text(
        json.dumps(
            {
                "mapping": {
                    recolor.rgb_to_hex(k): recolor.rgb_to_hex(v) for k, v in table.items()
                },
                "tolerance": tolerance,
                "smooth": smooth,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    old_file = d / "recolored.pdf"
    if old_file.exists():
        old_file.unlink()  # 让下载时按最新映射重新生成
    result = {
        "download": f"/api/download/{sid}",
        "matched": matched,
        "warnings": _pdf_shading_warning(src),
        "preview": preview,
    }
    (d / "apply.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    _set_meta(sid, status="ready")
    _set_progress(sid, 100, "完成")


def _finish_apply(sid, src, table, tolerance, smooth):
    ext = src.suffix.lower()
    d = _session_dir(sid)
    if ext == ".pdf":
        _finish_apply_pdf_preview(sid, src, table, tolerance, smooth)
        return
    out = d / ("recolored" + ext)
    stats = {"matched": 0}
    err = io.StringIO()
    _set_progress(sid, 5, "开始换色")
    with redirect_stderr(err):
        if ext == ".svg":
            try:
                text = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = src.read_text(encoding="latin-1")
            out.write_text(recolor.recolor_svg(text, table, stats), encoding="utf-8")
        elif ext in RASTER_EXTS:
            _set_progress(sid, 20, "平滑换色" if smooth else "精确换色")
            anchors = None
            if smooth:
                scan = _read_json(d / "scan.json") or {}
                anchors = [tuple(c["rgb"]) for c in (scan.get("colors") or [])][:10]
            recolor.recolor_raster(str(src), table, str(out), stats, tolerance, anchors)
        else:
            def cb(done, total):
                _set_progress(sid, 10 + int(done / max(total, 1) * 75), f"换色 {done}/{total}")
            recolor.apply_pdf(str(src), table, str(out), stats, cb)
    _set_progress(sid, 92, "生成预览")
    (d / "mapping.json").write_text(
        json.dumps(
            {
                "mapping": {
                    recolor.rgb_to_hex(k): recolor.rgb_to_hex(v) for k, v in table.items()
                },
                "tolerance": tolerance,
                "smooth": smooth,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result = {
        "download": f"/api/download/{sid}",
        "matched": stats["matched"],
        "warnings": err.getvalue().strip(),
        "preview": render_preview(out),
    }
    (d / "apply.json").write_text(
        json.dumps(result, ensure_ascii=False), encoding="utf-8"
    )
    _set_meta(sid, status="ready")
    _set_progress(sid, 100, "完成")


# ---------------------------------------------------------------- 路由

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/upload")
def api_upload():
    f = request.files.get("file")
    if f is None or not f.filename:
        return jsonify(error="没有收到文件"), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED:
        return jsonify(error="只支持 SVG / PDF / PNG / JPG / GIF / BMP / TIFF / WebP"), 400

    sid = uuid.uuid4().hex[:12]
    d = _session_dir(sid)
    d.mkdir()
    src = d / ("original" + ext)
    f.save(src)
    _set_meta(sid, name=f.filename, kind=ext[1:], ext=ext, status="processing", error=None)
    _start_job(sid, lambda: _finish_upload(sid, src, ext))
    return jsonify(id=sid, name=f.filename, kind=ext[1:], status="processing")


@app.get("/api/status/<sid>")
def api_status(sid):
    if not SID_RE.match(sid):
        return jsonify(error="无效的文件会话"), 404
    d = _session_dir(sid)
    m = _meta(sid)
    status = m.get("status", "error")
    out = {"status": status, "error": m.get("error")}
    prog = _read_json(d / "progress.json")
    if prog:
        out["progress"] = prog
    if status == "ready":
        scan = _read_json(d / "scan.json")
        if scan:
            out["scan"] = scan
        apply_result = _read_json(d / "apply.json")
        if apply_result:
            out["apply"] = apply_result
        mapping = _read_json(d / "mapping.json")
        if mapping:
            out["mapping"] = mapping
    return jsonify(out)


@app.get("/api/history")
def api_history():
    _cleanup_sessions()
    items = []
    for d in SESSIONS.iterdir():
        if not d.is_dir():
            continue
        meta = _read_json(d / "meta.json")
        if not meta:
            continue
        scan = _read_json(d / "scan.json", {}) or {}
        items.append(
            {
                "id": d.name,
                "name": meta.get("name", d.name),
                "kind": meta.get("kind", ""),
                "ext": meta.get("ext", ""),
                "time": int(d.stat().st_mtime * 1000),
                "colors": len(scan.get("colors", [])),
            }
        )
    items.sort(key=lambda x: -x["time"])
    return jsonify(items=items)


@app.delete("/api/history/<sid>")
def api_history_delete(sid):
    if not SID_RE.match(sid):
        return jsonify(error="无效的会话"), 404
    d = _session_dir(sid)
    if not d.exists():
        return jsonify(ok=True)  # 已不存在视为删除成功
    with _active_lock:
        busy = sid in _active_jobs
    if busy:
        return jsonify(error="文件正在处理中，请稍后再删除"), 409
    last_err = None
    for _ in range(5):
        try:
            shutil.rmtree(d)
            return jsonify(ok=True)
        except Exception as e:
            last_err = e
            time.sleep(0.3)
    return jsonify(error=f"删除失败：{last_err}"), 500


@app.post("/api/apply")
def api_apply():
    data = request.get_json(force=True, silent=True) or {}
    sid = str(data.get("id", ""))
    if not SID_RE.match(sid):
        return jsonify(error="无效的文件会话，请重新上传"), 400
    d = _session_dir(sid)
    m = _meta(sid)
    ext = m.get("ext")
    if ext not in ALLOWED:
        return jsonify(error="文件已失效，请重新上传"), 404
    src = d / ("original" + ext)
    if not src.exists():
        return jsonify(error="文件已失效，请重新上传"), 404

    mapping = data.get("mapping") or {}
    table = {}
    for k, v in mapping.items():
        try:
            table[recolor.color_to_rgb(k)] = recolor.color_to_rgb(v)
        except ValueError:
            continue
    if not table:
        return jsonify(error="映射为空：请至少指定一组 原色 → 新色"), 400

    try:
        tolerance = max(0, min(255, int(data.get("tolerance", 0) or 0)))
    except (TypeError, ValueError):
        tolerance = 0
    smooth = bool(data.get("smooth", False))

    _set_meta(sid, status="applying", error=None)
    _start_job(sid, lambda: _finish_apply(sid, src, table, tolerance, smooth))
    return jsonify(status="applying")


@app.get("/api/download/<sid>")
def api_download(sid):
    if not SID_RE.match(sid):
        abort(404)
    d = _session_dir(sid)
    m = _meta(sid)
    ext = m.get("ext")
    if ext not in ALLOWED:
        abort(404)
    out = d / ("recolored" + ext)
    if not out.exists():
        # PDF：应用配色时只出了预览，下载时才做真正的矢量换色+压缩（只做一次）
        if ext != ".pdf":
            abort(404)
        cfg = _read_json(d / "mapping.json", {}) or {}
        if not cfg.get("mapping"):
            return jsonify(error="请先应用配色"), 409
        with _active_lock:
            if sid in _active_jobs:
                return jsonify(error="文件正在处理中，请稍后再试"), 409
            _active_jobs.add(sid)
        table = {}
        for k, v in cfg["mapping"].items():
            try:
                table[recolor.color_to_rgb(k)] = recolor.color_to_rgb(v)
            except ValueError:
                continue
        src = d / ("original" + ext)
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                recolor.apply_pdf(
                    str(src),
                    table,
                    str(out),
                    {"matched": 0},
                )
        finally:
            with _active_lock:
                _active_jobs.discard(sid)
    return send_file(out, as_attachment=True, download_name=m.get("name") or out.name)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8377"))
    _cleanup_sessions()
    # 默认只在本机访问；部署时设置 HOST=0.0.0.0
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port, threaded=True)
