#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gradio 版一键换色（Hugging Face Spaces 部署，SDK=Gradio，兼容 ZeroGPU）。

界面尽量贴近 Flask 网页版：
- 主图预览占主要区域，右侧小文件上传框 + 颜色映射表
- 映射表“原颜色”列带色块；点击“原颜色 / 替换颜色”列即高亮对应颜色
- 应用配色后自动刷新预览，支持 原图 / 换色后 切换
- 导出 PDF / SVG / PNG / 颜色代码（CSV / JSON），PDF 懒加载生成
"""

import csv
import io
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

import gradio as gr
import numpy as np
from PIL import Image

import recolor

# ZeroGPU 要求主模块至少有一个 @spaces.GPU 函数（纯 CPU 工具，仅占位，不会被调用）。
try:
    import spaces

    @spaces.GPU
    def _zerogpu_stub(_unused=0):
        return 0

except Exception:
    pass

SESSIONS = Path(
    os.environ.get("SESSIONS_DIR", str(Path(tempfile.gettempdir()) / "recolor-sessions"))
)
SESSIONS.mkdir(exist_ok=True)
RASTER_EXTS = recolor.RASTER_EXTS
ALLOWED = {".pdf", ".svg"} | RASTER_EXTS
HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
BADGE = {"fill": "填充", "stroke": "描边", "fill+stroke": "填充+描边", "pixel": "像素"}
SHOW_MAX = 300


def norm(h):
    return (h or "").strip().lower()


def rgb_from_hex(h):
    h = norm(h).lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    return [int(h[i : i + 2], 16) for i in (0, 2, 4)]


def swatch_html(hex_color):
    """把颜色码渲染成“色块 + 颜色码”，用于映射表的 markdown 单元格。"""
    h = norm(hex_color)
    return (
        '<span style="display:inline-block;width:14px;height:14px;border-radius:3px;'
        f'background:{h};vertical-align:-2px;margin-right:6px;'
        'border:1px solid rgba(0,0,0,.35)"></span>' + h
    )


def scan_colors(path, ext, on_progress=None):
    if ext == ".svg":
        colors = recolor.scan_svg_colors(path.read_text(encoding="utf-8", errors="replace"))
        total = len(colors)
    elif ext == ".pdf":
        colors = recolor.scan_pdf_colors(str(path), on_progress)
        total = len(colors)
    else:
        colors, total = recolor.scan_raster_colors(str(path), limit=800)
    items = []
    for rgb, info in sorted(colors.items(), key=lambda kv: -kv[1]["count"])[:SHOW_MAX]:
        fc = info.get("fill_count", 0)
        sc = info.get("stroke_count", 0)
        kind = "fill+stroke" if fc and sc else ("stroke" if sc else "fill")
        if ext in RASTER_EXTS:
            kind = "pixel"
        items.append(
            {
                "hex": recolor.rgb_to_hex(rgb),
                "rgb": list(rgb),
                "count": info["count"],
                "opacities": sorted(info.get("opacities") or []),
                "kind": kind,
            }
        )
    return items, total


def preview_np(path, max_w=1000):
    """渲染第一页/整图成 RGB numpy（PDF/SVG 用 fitz，位图用 PIL）。"""
    ext = Path(path).suffix.lower()
    if ext in RASTER_EXTS:
        im = Image.open(path).convert("RGB")
        if im.width > max_w:
            im = im.resize((max_w, int(im.height * max_w / im.width)), Image.LANCZOS)
        return np.asarray(im)
    import fitz

    doc = fitz.open(path)
    try:
        page = doc[0]
        zoom = min(2.0, max_w / max(page.rect.width, 1.0))
        pm = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width, 3)
        return arr.copy()
    finally:
        doc.close()


def preview_background(arr):
    packed = (
        arr[..., 0].astype(np.uint32) << 16
        | arr[..., 1].astype(np.uint32) << 8
        | arr[..., 2].astype(np.uint32)
    )
    vals, counts = np.unique(packed, return_counts=True)
    p = int(vals[np.argmax(counts)])
    return "#%02x%02x%02x" % ((p >> 16) & 255, (p >> 8) & 255, p & 255)


def map_preview(arr, table, tolerance, smooth, anchors):
    stats = {"matched": 0}
    new = recolor.map_rgb_array(arr, table, tolerance, anchors if smooth else None, stats)
    return new, stats["matched"]


def highlight_np(arr, rgb, opacities, tol=30):
    """与 Flask 版一致的精简高亮：目标色 + 真实透明度混合候选，保护白色背景。"""
    out = arr.copy()
    cands = [rgb]
    for a in opacities or []:
        a = max(0.0, min(1.0, float(a)))
        cands.append(
            [
                round(rgb[0] * a + 255 * (1 - a)),
                round(rgb[1] * a + 255 * (1 - a)),
                round(rgb[2] * a + 255 * (1 - a)),
            ]
        )
    fa = out.astype(np.int16)
    best = np.full((arr.shape[0], arr.shape[1]), 10**9, dtype=np.int32)
    for c in cands:
        d = np.abs(fa[..., 0] - c[0]) + np.abs(fa[..., 1] - c[1]) + np.abs(fa[..., 2] - c[2])
        np.minimum(best, d, out=best)
    mask = best <= tol
    # 白色/浅色背景保持原样，避免被“目标色+白色混合”候选误命中全图提亮
    white = (fa[..., 0] > 235) & (fa[..., 1] > 235) & (fa[..., 2] > 235)
    mask = mask & ~white
    out[mask] = np.minimum(255, out[mask].astype(np.int16) + 80).astype(np.uint8)
    dim = ~mask & ~white
    out[dim] = (out[dim].astype(np.float32) * 0.55).astype(np.uint8)
    return out, int(mask.sum())


def build_table(mapping_hex):
    table = {}
    for k, v in mapping_hex.items():
        try:
            table[recolor.color_to_rgb(k)] = recolor.color_to_rgb(v)
        except ValueError:
            continue
    return table


def is_minor(item, max_count):
    if item["kind"] == "stroke":
        return True
    if item["kind"] == "pixel" and item["count"] < max(50, round(max_count * 0.001)):
        return True
    return False


def build_df(items, replace=None):
    """把颜色列表转成映射表行：[[色块+码, 替换码, 类型, 次数], ...]"""
    replace = replace or {}
    return [
        [
            swatch_html(it["hex"]),
            replace.get(it["hex"], it["hex"]),
            BADGE.get(it["kind"], it["kind"]),
            it["count"],
        ]
        for it in items
    ]


# ---------------------------------------------------------------- 事件处理

def on_upload(file, progress=gr.Progress()):
    if file is None:
        return gr.update(), gr.update(), gr.update(), None, "请先上传文件", ""
    path = Path(file) if isinstance(file, (str, Path)) else Path(file.name)
    ext = path.suffix.lower()
    if ext not in ALLOWED:
        return gr.update(), gr.update(), gr.update(), None, f"不支持 {ext}，请上传 SVG/PDF/图片", ""
    sid = uuid.uuid4().hex[:12]
    d = SESSIONS / sid
    d.mkdir()
    src = d / ("original" + ext)
    shutil.copyfile(str(path), str(src))

    progress(0.05, desc="扫描颜色")
    if ext == ".pdf":
        def cb(done, total):
            progress(0.05 + 0.6 * done / max(total, 1), desc=f"扫描内容流 {done}/{total}")

        items, total = scan_colors(src, ext, cb)
    else:
        progress(0.3, desc="统计颜色")
        items, total = scan_colors(src, ext)
    progress(0.8, desc="生成预览")
    before = preview_np(src)
    bg = preview_background(before)
    max_count = max([it["count"] for it in items] or [0])
    for it in items:
        it["minor"] = is_minor(it, max_count)
    state = {
        "sid": sid,
        "ext": ext,
        "name": path.name,
        "before": before,
        "after": None,
        "bg": bg,
        "items": items,
        "view": "before",
        "replace": {},
    }
    progress(1.0, desc="完成")
    status = f"扫描完成：共 {total} 种颜色（展示前 {len(items)} 种）"
    return build_df(items), before, gr.update(value="原图"), state, status, f"检测到背景色：{bg}"


def on_apply(state, df, protect, smooth, tolerance, progress=gr.Progress()):
    if not state:
        return gr.update(), gr.update(), "请先上传文件", gr.update()
    mapping_hex = {}
    items = state["items"]
    if df is not None and hasattr(df, "values"):
        rows = df.values.tolist()
        for i, r in enumerate(rows):
            if i < len(items):
                old = norm(items[i]["hex"])
            else:
                m = HEX_RE.search(str(r[0]))
                old = norm(m.group(0)) if m else ""
            new = norm(str(r[1]))
            if old == new or not HEX_RE.match(old) or not HEX_RE.match(new):
                continue
            if protect and state.get("bg") and old == norm(state["bg"]):
                continue
            mapping_hex[old] = new
    if not mapping_hex:
        return gr.update(), gr.update(), "没有有效的颜色映射：请修改“替换颜色”列", gr.update()
    anchors = [tuple(it["rgb"]) for it in items][:10] if smooth else None
    progress(0.2, desc="应用配色")
    after, matched = map_preview(state["before"], build_table(mapping_hex), int(tolerance or 0), smooth, anchors)
    state["after"] = after
    state["mapping"] = mapping_hex
    (SESSIONS / state["sid"] / "mapping.json").write_text(
        json.dumps(
            {"mapping": mapping_hex, "tolerance": int(tolerance or 0), "smooth": smooth},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    progress(1.0, desc="完成")
    status = f"已应用：替换约 {matched} 像素（预览）。点“导出”生成最终文件"
    return after, gr.update(value="换色后"), status, gr.update()


def on_select(evt: gr.SelectData, state, view):
    try:
        if not state:
            return gr.update(), gr.update(), "请先上传文件"
        idx = evt.index
        r, c = (idx.get("row"), idx.get("col")) if isinstance(idx, dict) else idx
        items = state["items"]
        if r is None or r >= len(items):
            return gr.update(), gr.update(), ""
        if c == 0:
            hex_color = items[r]["hex"]
            target_view = "原图"
        elif c == 1:
            hex_color = norm(str(evt.value))
            if not HEX_RE.match(hex_color):
                return gr.update(), gr.update(), "替换颜色格式应为 #RRGGBB"
            if state.get("after") is None:
                return gr.update(), gr.update(), "请先应用配色，再在“换色后”视图高亮替换色"
            target_view = "换色后"
        else:
            return gr.update(), gr.update(), ""
        base = state["before"] if target_view == "原图" else state["after"]
        img, matched = highlight_np(base, rgb_from_hex(hex_color), items[r].get("opacities"))
        if not matched:
            return gr.update(), gr.update(), f"图中未找到 {hex_color}"
        return img, gr.update(value=target_view), f"已高亮 {hex_color}（{matched} 像素），再点一次取消"
    except Exception as e:
        return gr.update(), gr.update(), f"高亮失败：{type(e).__name__}: {e}"


def on_view(view, state):
    return on_cancel_hl(state, view)


def on_cancel_hl(state, view):
    if not state:
        return gr.update()
    base = state["after"] if view == "换色后" and state.get("after") is not None else state["before"]
    return base


def on_show_minor(show, state):
    if not state:
        return gr.update(), ""
    items = [it for it in state["items"] if show or not it.get("minor")]
    hidden = sum(1 for it in state["items"] if it.get("minor"))
    note = "" if show else f"已隐藏 {hidden} 种次要颜色（细线/边缘/过渡）"
    return build_df(items, state.get("replace", {})), note


def on_batch(text, state):
    if not state:
        return gr.update(), "请先上传文件"
    pairs = []
    for line in (text or "").splitlines():
        parts = re.split(r"[\s,;，；]+", line.strip())
        if len(parts) >= 2:
            pairs.append((norm(parts[0]), norm(parts[1])))
    if not pairs:
        return gr.update(), "没有解析到映射，每行格式：原色 新色"
    items = list(state["items"])
    replace = dict(state.get("replace") or {})
    for old, new in pairs:
        if not HEX_RE.match(old) or not HEX_RE.match(new):
            continue
        replace[old] = new
        if old not in {it["hex"] for it in items}:
            items.append({"hex": old, "rgb": rgb_from_hex(old), "count": 0, "opacities": [], "kind": "fill", "minor": False})
    state["items"] = items
    state["replace"] = replace
    return build_df(items, replace), f"已填入 {len(pairs)} 组映射"


def parse_mapping_file(file):
    """解析映射文件：CSV 第一列原色、第二列替换色（不读表头，表头自然被过滤）；
    也支持 JSON（{原色: 新色}）和 txt（每行：原色 新色）。"""
    path = Path(file) if isinstance(file, (str, Path)) else Path(file.name)
    pairs = []
    ext = path.suffix.lower()
    if ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            pairs = [(norm(str(k)), norm(str(v))) for k, v in data.items()]
    else:
        text = path.read_text(encoding="utf-8", errors="replace").lstrip("\ufeff")
        if ext == ".csv":
            for row in csv.reader(io.StringIO(text)):
                if len(row) >= 2:
                    pairs.append((norm(row[0]), norm(row[1])))
        else:
            for line in text.splitlines():
                parts = re.split(r"[\s,;，；\t]+", line.strip())
                if len(parts) >= 2:
                    pairs.append((norm(parts[0]), norm(parts[1])))
    return [(o, n) for o, n in pairs if HEX_RE.match(o) and HEX_RE.match(n)]


def on_map_file(file, state):
    if not state:
        return gr.update(), "请先上传文件"
    try:
        pairs = parse_mapping_file(file)
    except Exception as e:
        return gr.update(), f"解析映射文件失败：{e}"
    if not pairs:
        return gr.update(), "没有解析到有效映射：CSV 第一列原色、第二列替换色（不读表头，表头会被自动跳过）"
    items = list(state["items"])
    replace = dict(state.get("replace") or {})
    for old, new in pairs:
        replace[old] = new
        if old not in {it["hex"] for it in items}:
            items.append(
                {"hex": old, "rgb": rgb_from_hex(old), "count": 0, "opacities": [], "kind": "fill", "minor": False}
            )
    state["items"] = items
    state["replace"] = replace
    return build_df(items, replace), f"已从文件载入 {len(pairs)} 组映射"


def _saved_mapping(state):
    cfg = json.loads((SESSIONS / state["sid"] / "mapping.json").read_text(encoding="utf-8"))
    return build_table(cfg["mapping"]), cfg.get("tolerance", 0), cfg.get("smooth", True)


def generate_file(state, ext, progress=gr.Progress()):
    """按映射生成最终文件（PDF 懒加载，带进度）。"""
    d = SESSIONS / state["sid"]
    src = d / ("original" + state["ext"])
    out = d / ("recolored" + ext)
    cfg = json.loads((d / "mapping.json").read_text(encoding="utf-8"))
    table = build_table(cfg["mapping"])
    stats = {"matched": 0}
    if ext == ".svg":
        text = src.read_text(encoding="utf-8", errors="replace")
        out.write_text(recolor.recolor_svg(text, table, stats), encoding="utf-8")
    elif ext in RASTER_EXTS:
        anchors = None
        if cfg.get("smooth"):
            anchors = [tuple(it["rgb"]) for it in state["items"]][:10]
        recolor.recolor_raster(str(src), table, str(out), stats, int(cfg.get("tolerance", 0) or 0), anchors)
    else:
        def cb(done, total):
            progress(0.1 + 0.8 * done / max(total, 1), desc=f"矢量换色 {done}/{total}")

        recolor.apply_pdf(str(src), table, str(out), stats, cb)
    progress(1.0, desc="完成")
    return out


def on_export(state, fmt, progress=gr.Progress()):
    if not state:
        return gr.update(), "请先上传文件"
    if not state.get("mapping") and not (SESSIONS / state["sid"] / "mapping.json").exists():
        return gr.update(), "请先应用配色"
    d = SESSIONS / state["sid"]
    ext = state["ext"]
    stem = Path(state["name"]).stem
    try:
        if fmt == "颜色代码 CSV":
            table, _, _ = _saved_mapping(state)
            head = "原颜色,替换颜色"
            body = "\n".join(f"{k},{v}" for k, v in table.items())
            p = d / "mapping.csv"
            p.write_text("\ufeff" + head + "\r\n" + body, encoding="utf-8")
            return str(p), f"已导出 {len(table)} 组颜色映射 → {p.name}"
        if fmt == "颜色代码 JSON":
            cfg = json.loads((d / "mapping.json").read_text(encoding="utf-8"))
            p = d / "mapping-codes.json"
            p.write_text(json.dumps(cfg["mapping"], ensure_ascii=False, indent=2), encoding="utf-8")
            return str(p), f"已导出 {len(cfg['mapping'])} 组颜色映射 → mapping-codes.json"
        if fmt == "PDF":
            if ext == ".pdf":
                out = d / "recolored.pdf"
                if not out.exists():
                    progress(0.05, desc="生成 PDF")
                    out = generate_file(state, ".pdf")
            elif ext == ".svg":
                vec = d / "recolored.svg"
                if not vec.exists():
                    vec = generate_file(state, ".svg")
                import fitz

                doc = fitz.open(str(vec))
                pdf_bytes = doc.convert_to_pdf()
                doc.close()
                out = d / "recolored.pdf"
                out.write_bytes(pdf_bytes)
            else:
                return gr.update(), "位图无法导出为 PDF/SVG"
            return str(out), f"已生成 {out.name}"
        if fmt == "SVG":
            out = generate_file(state, ".svg")
            return str(out), f"已生成 {out.name}"
        if fmt == "PNG":
            out = d / "recolored.png"
            if ext in RASTER_EXTS:
                out = generate_file(state, ext)
            else:
                import fitz

                vec = d / ("recolored." + ("svg" if ext == ".svg" else "pdf"))
                if not vec.exists():
                    vec = generate_file(state, "." + ("svg" if ext == ".svg" else "pdf"))
                doc = fitz.open(str(vec))
                page = doc[0]
                pm = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                pm.save(str(out))
                doc.close()
            return str(out), f"已生成 {out.name}"
        return gr.update(), f"未知格式：{fmt}"
    except Exception as e:
        return gr.update(), f"导出失败：{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 界面

css = """
#upload-box { min-height: 48px !important; }
#upload-box .wrap { min-height: 42px !important; }
#upload-box label { font-size: 12px !important; }
.stage-img img { max-height: 70vh; }
"""

GRADIO_MAJOR = int(getattr(gr, "__version__", "5").split(".")[0])
blocks_kwargs = {"title": "一键换色"}
launch_kwargs = {}
if GRADIO_MAJOR >= 6:
    launch_kwargs = {"theme": gr.themes.Soft(), "css": css}
else:
    blocks_kwargs = {"title": "一键换色", "theme": gr.themes.Soft(), "css": css}

with gr.Blocks(**blocks_kwargs) as demo:
    gr.Markdown(
        "# SVG / PDF / 图片一键换色\n"
        "上传文件 → 映射表带色块，**点击“原颜色 / 替换颜色”列即高亮对应颜色** → "
        "应用配色自动刷新预览 → 导出 PDF / SVG / PNG / 颜色代码。"
    )
    state = gr.State(None)
    with gr.Row():
        with gr.Column(scale=3, elem_classes=["stage-img"]):
            view = gr.Radio(["原图", "换色后"], value="原图", label="视图", interactive=True)
            view_img = gr.Image(label="预览", interactive=False)
            cancel_hl = gr.Button("取消高亮")
        with gr.Column(scale=1):
            file_in = gr.File(
                label="上传 SVG / PDF / 图片（可直接拖拽到框内）",
                file_types=[".svg", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"],
                file_count="single",
                elem_id="upload-box",
            )
            status = gr.Markdown()
            bg_label = gr.Markdown()
            gr.Markdown("### 颜色映射（点列高亮）")
            colors_df = gr.Dataframe(
                headers=["原颜色", "替换颜色", "类型", "次数"],
                datatype=["markdown", "str", "str", "number"],
                interactive=True,
                label="",
            )
            show_minor = gr.Checkbox(value=False, label="显示次要颜色（细线/边缘/过渡）")
            minor_note = gr.Markdown()
            with gr.Accordion("批量填入映射（粘贴 / CSV / JSON / txt）", open=False):
                batch_area = gr.Textbox(lines=3, placeholder="#e41a1c #00b8d9", label="每行：原色 新色")
                batch_btn = gr.Button("填入映射")
                map_file = gr.File(
                    label="或上传映射文件（CSV 第一列原色、第二列替换色，不读表头）",
                    file_types=[".csv", ".json", ".txt"],
                    file_count="single",
                )
            with gr.Row():
                protect_bg = gr.Checkbox(value=True, label="不替换背景色")
                smooth = gr.Checkbox(value=True, label="平滑换色")
            tolerance = gr.Number(value=0, minimum=0, maximum=255, precision=0, label="容差（仅精确模式）")
            apply_btn = gr.Button("应用配色（刷新预览）", variant="primary")
            with gr.Row():
                export_fmt = gr.Dropdown(
                    ["PDF", "SVG", "PNG", "颜色代码 CSV", "颜色代码 JSON"],
                    value="PDF",
                    label="导出格式",
                )
                export_btn = gr.Button("导出", variant="primary")
            file_out = gr.File(label="下载")

    file_in.upload(
        on_upload,
        inputs=[file_in],
        outputs=[colors_df, view_img, view, state, status, bg_label],
    )
    colors_df.select(
        on_select,
        inputs=[state, view],
        outputs=[view_img, view, status],
    )
    apply_btn.click(
        on_apply,
        inputs=[state, colors_df, protect_bg, smooth, tolerance],
        outputs=[view_img, view, status, file_out],
    )
    view.change(on_view, inputs=[view, state], outputs=[view_img])
    cancel_hl.click(on_cancel_hl, inputs=[state, view], outputs=[view_img])
    show_minor.change(on_show_minor, inputs=[show_minor, state], outputs=[colors_df, minor_note])
    batch_btn.click(on_batch, inputs=[batch_area, state], outputs=[colors_df, status])
    map_file.upload(on_map_file, inputs=[map_file, state], outputs=[colors_df, status])
    export_btn.click(on_export, inputs=[state, export_fmt], outputs=[file_out, status])


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=4).launch(**launch_kwargs)
