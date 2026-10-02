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
import webbrowser
from contextlib import redirect_stderr
from pathlib import Path

import fitz
from flask import Flask, abort, jsonify, render_template, request, send_file

if getattr(sys, "frozen", False):
    # PyInstaller 打包：程序文件在 exe 旁边，模板/静态在 _MEIPASS 解压目录
    BASE = Path(sys.executable).resolve().parent
    RES_ROOT = Path(getattr(sys, "_MEIPASS", BASE))
    sys.path.insert(0, str(BASE.parent))
else:
    BASE = Path(__file__).resolve().parent
    RES_ROOT = BASE
    sys.path.insert(0, str(BASE.parent))
import recolor  # noqa: E402

def _default_sessions_dir():
    """exe 运行时默认存到用户目录，避免“从临时目录解压启动后被系统清理”导致
    会话目录/运行时文件丢失；开发版仍存在 web/sessions。"""
    if getattr(sys, "frozen", False):
        try:
            local = os.environ.get("LOCALAPPDATA")
            if local:
                d = Path(local) / "RecolorTool" / "sessions"
                d.mkdir(parents=True, exist_ok=True)
                return d
        except Exception:
            pass
    return BASE / "sessions"


SESSIONS = Path(os.environ.get("SESSIONS_DIR", str(_default_sessions_dir())))
SESSIONS.mkdir(parents=True, exist_ok=True)
RASTER_EXTS = recolor.RASTER_EXTS
ALLOWED = {".pdf", ".svg"} | RASTER_EXTS
SID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "500")) * 1024 * 1024
HISTORY_DAYS = int(os.environ.get("HISTORY_DAYS", "7"))
IDLE_EXIT_SECONDS = int(os.environ.get("IDLE_EXIT_SECONDS", "120"))  # 0=关闭空闲自动退出
_active_jobs = set()
_active_lock = threading.Lock()
_SERVER = None
_SHUTDOWN = threading.Event()
_LAST_PING = 0.0
_PING_SEEN = False

app = Flask(
    __name__,
    template_folder=str(RES_ROOT / "templates"),
    static_folder=str(RES_ROOT / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # 前端静态文件不缓存，改完刷新即生效


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
    pages = None
    if ext == ".pdf":
        try:
            doc = fitz.open(src)
            pages = doc.page_count
            doc.close()
        except Exception:
            pages = None
    result = {"colors": items, "total": total, "pages": pages, "preview": render_preview(src)}
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


def _finish_generate(sid, src, ext):
    """在后台生成最终文件（PDF 矢量换色 + 压缩），带进度。"""
    d = _session_dir(sid)
    cfg = _read_json(d / "mapping.json", {}) or {}
    if not cfg.get("mapping"):
        raise RuntimeError("请先应用配色，再生成文件")
    table = {}
    for k, v in cfg["mapping"].items():
        try:
            table[recolor.color_to_rgb(k)] = recolor.color_to_rgb(v)
        except ValueError:
            continue
    out = d / ("recolored" + ext)
    stats = {"matched": 0}
    err = io.StringIO()

    def cb(done, total):
        _set_progress(sid, 10 + int(done / max(total, 1) * 80), f"生成最终文件 {done}/{total}")

    with redirect_stderr(err):
        if ext == ".pdf":
            recolor.apply_pdf(str(src), table, str(out), stats, cb)
        else:
            text = src.read_text(encoding="utf-8", errors="replace")
            out.write_text(recolor.recolor_svg(text, table, stats), encoding="utf-8")
    warnings = err.getvalue().strip()
    if warnings:
        _set_meta(sid, last_warnings=warnings)
    _set_progress(sid, 100, "完成")
    _set_meta(sid, status="ready", error=None)


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
    SESSIONS.mkdir(parents=True, exist_ok=True)
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


@app.post("/api/generate/<sid>")
def api_generate(sid):
    """PDF 懒加载：应用配色只出预览，点下载时后台生成真正的矢量文件。"""
    if not SID_RE.match(sid):
        return jsonify(error="无效的文件会话"), 404
    d = _session_dir(sid)
    m = _meta(sid)
    ext = m.get("ext")
    if ext != ".pdf":
        return jsonify(error="该格式在应用配色时已生成文件，直接下载即可"), 400
    out = d / "recolored.pdf"
    if out.exists():
        return jsonify(status="ready")  # 已生成过，直接下载
    src = d / "original.pdf"
    if not src.exists():
        return jsonify(error="文件已失效，请重新上传"), 404
    with _active_lock:
        if sid in _active_jobs:
            return jsonify(error="文件正在生成中，请稍候"), 409
    cfg = _read_json(d / "mapping.json", {}) or {}
    if not cfg.get("mapping"):
        return jsonify(error="请先应用配色"), 409
    _set_meta(sid, status="generating", error=None)
    _start_job(sid, lambda: _finish_generate(sid, src, ext))
    return jsonify(status="generating")


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
        if ext != ".pdf":
            abort(404)
        return jsonify(error="最终文件尚未生成，请先点击下载按钮（会自动生成）"), 409
    return send_file(out, as_attachment=True, download_name=m.get("name") or out.name)


@app.get("/api/export/<sid>/<fmt>")
def api_export(sid, fmt):
    """按选定格式导出：pdf / svg / png（映射表 CSV/JSON 由前端本地生成）。"""
    if not SID_RE.match(sid):
        abort(404)
    d = _session_dir(sid)
    m = _meta(sid)
    ext = m.get("ext")
    if ext not in ALLOWED:
        abort(404)
    src = d / ("original" + ext)
    if not src.exists():
        abort(404)
    stem = Path(m.get("name") or "recolored").stem

    if fmt not in ("pdf", "svg", "png"):
        return jsonify(error=f"不支持的导出格式: {fmt}"), 400

    recolored = d / ("recolored" + ext)
    if ext == ".pdf" and not recolored.exists():
        return jsonify(error="最终文件尚未生成，请先点击导出（会自动生成）"), 409

    try:
        # ---- PDF ----
        if fmt == "pdf":
            if ext == ".pdf":
                return send_file(recolored, as_attachment=True, download_name=f"{stem}-new.pdf")
            if ext == ".svg":
                doc = fitz.open(str(recolored))
                pdf_bytes = doc.convert_to_pdf()
                doc.close()
                return send_file(
                    io.BytesIO(pdf_bytes), as_attachment=True, download_name=f"{stem}-new.pdf"
                )
            return jsonify(error="位图无法导出为 PDF/SVG"), 400

        # ---- SVG ----
        if fmt == "svg":
            if ext == ".svg":
                return send_file(recolored, as_attachment=True, download_name=f"{stem}-new.svg")
            if ext == ".pdf":
                doc = fitz.open(str(recolored))
                svg = doc[0].get_svg_image()
                doc.close()
                data = svg.encode("utf-8") if isinstance(svg, str) else bytes(svg)
                return send_file(
                    io.BytesIO(data), as_attachment=True, download_name=f"{stem}-new.svg"
                )
            return jsonify(error="位图无法导出为 PDF/SVG"), 400

        # ---- PNG ----
        if ext in RASTER_EXTS:
            return send_file(recolored, as_attachment=True, download_name=f"{stem}-new.png")
        try:
            scale = max(0.5, min(6.0, float(request.args.get("scale", "2.0"))))
        except ValueError:
            scale = 2.0
        doc = fitz.open(str(recolored))
        page = doc[0]
        pm = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        png = pm.tobytes("png")
        doc.close()
        return send_file(io.BytesIO(png), as_attachment=True, download_name=f"{stem}-new.png")
    except Exception as e:
        return jsonify(error=f"导出失败：{e}"), 500


@app.post("/api/ping")
def api_ping():
    """前端心跳：页面还在就报活，用于空闲自动退出。"""
    global _LAST_PING, _PING_SEEN
    if _SHUTDOWN.is_set():
        # 已决定退出：拒绝心跳，避免另一个标签页把要退出的实例“救活”
        return jsonify(ok=False, shutting_down=True), 410
    _LAST_PING = time.time()
    _PING_SEEN = True
    return jsonify(ok=True)


@app.post("/api/shutdown")
def api_shutdown():
    """退出：置信号，由主线程统一收尾（关窗口 + 关服务）。"""
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify(error="拒绝"), 403
    _SHUTDOWN.set()
    return jsonify(ok=True)


def _state_dir():
    """运行时目录：%LOCALAPPDATA%\\RecolorTool（日志、会话都在这里）。"""
    base = os.environ.get("RECOLOR_STATE_DIR")
    if not base:
        local = os.environ.get("LOCALAPPDATA")
        base = str(Path(local) / "RecolorTool") if local else str(BASE)
    try:
        Path(base).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return Path(base)


def _cleanup_legacy_files():
    """清掉旧版“抢占端口”机制留下的 port.txt / pid.txt。"""
    for name in ("port.txt", "pid.txt"):
        try:
            (_state_dir() / name).unlink()
        except Exception:
            pass


def _idle_watchdog():
    """页面长时间没动静就退出（IDLE_EXIT_SECONDS=0 关闭）。
    收到过第一次心跳之后才开始计时，避免页面加载慢时误杀。"""
    if IDLE_EXIT_SECONDS <= 0:
        return
    while not _SHUTDOWN.is_set():
        time.sleep(1.0)
        if not _PING_SEEN:
            continue
        if time.time() - _LAST_PING > IDLE_EXIT_SECONDS:
            _log(f"空闲 {IDLE_EXIT_SECONDS}s，自动退出")
            _SHUTDOWN.set()
            return


def _run_ui(url):
    """打开界面并阻塞到用户关掉它：窗口模式关窗口即返回，进程随之退出。"""
    if os.environ.get("RECOLOR_UI", "window") == "window":
        try:
            import webview  # 不在模块顶部 import：开发环境可能没装

            window = webview.create_window("一键换色", url, width=1280, height=860)

            def _watch():
                _SHUTDOWN.wait()
                time.sleep(0.2)
                try:
                    window.destroy()
                except Exception:
                    pass

            webview.start(_watch)  # 主线程阻塞，直到窗口关闭
            return
        except Exception:
            import traceback

            _log("窗口模式不可用，回退浏览器：" + traceback.format_exc().replace("\n", " | "))
    if os.environ.get("AUTO_OPEN", "1") == "1":
        webbrowser.open(url)
    _SHUTDOWN.wait()


def _log(msg):
    """写启动日志（%LOCALAPPDATA%\\RecolorTool\\startup.log），方便排查“没反应”。"""
    try:
        with (_state_dir() / "startup.log").open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _fatal(msg):
    """窗口程序没有控制台，启动失败时写日志并弹窗提示，避免“没反应”。"""
    try:
        (_state_dir() / "error.log").write_text(msg, encoding="utf-8")
    except Exception:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, msg, "一键换色启动失败", 0x10)
    except Exception:
        pass


def _main():
    global _SERVER
    from werkzeug.serving import make_server

    _log("启动中...")
    _cleanup_legacy_files()
    threading.Thread(target=_cleanup_sessions, daemon=True).start()  # 不挡启动

    host = os.environ.get("HOST", "127.0.0.1")
    # 默认端口 0 = 让系统分配空闲端口：不可能与别的实例或别的软件碰撞，也就不存在
    # “端口一直被占用”。需要固定端口（局域网/开发调试）时设 PORT，例如 PORT=9000。
    want = int(os.environ.get("PORT", "0"))
    _SERVER = make_server(host, want, app, threaded=True)
    port = _SERVER.port  # 真实端口（传 0 时由 werkzeug 回填）
    threading.Thread(target=_SERVER.serve_forever, daemon=True).start()
    threading.Thread(target=_idle_watchdog, daemon=True).start()

    browser_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{browser_host}:{port}"
    _log(f"服务已启动: {url}")
    try:
        print(f"一键换色已启动: {url}  (关窗口即退出)")
    except Exception:
        pass  # 无控制台（windowed exe）时 stdout 可能为 None
    try:
        _run_ui(url)
    finally:
        try:
            _SERVER.shutdown()
        except Exception:
            pass
        try:
            _SERVER.server_close()
        except Exception:
            pass
        _log("服务已停止")


if __name__ == "__main__":
    try:
        _main()
    except Exception:
        import traceback

        tb = traceback.format_exc()
        _log("启动失败：" + tb.replace("\n", " | "))
        _fatal(tb)
        raise
