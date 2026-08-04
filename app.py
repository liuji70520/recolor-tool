#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gradio 版一键换色（用于 Hugging Face Spaces 部署，SDK=Gradio）。

功能：上传 SVG/PDF/图片 → 自动扫色（标注填充/描边）→ 编辑替换色 →
应用配色预览（快）→ 定位高亮 → 生成下载文件。
"""

import base64
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

# Hugging Face ZeroGPU 要求主模块在启动时至少检测到一个 @spaces.GPU 函数。
# 本工具是纯 CPU 计算（扫描/换色/预览），这里只是一个满足检查的空占位，
# 不会被任何 UI 事件调用，因此不会真正占用 GPU。
try:
    import spaces

    @spaces.GPU
    def _zerogpu_stub(_unused=0):
        return 0

except Exception:
    pass

SESSIONS = Path(os.environ.get("SESSIONS_DIR", str(Path(tempfile.gettempdir()) / "recolor-sessions")))
SESSIONS.mkdir(exist_ok=True)
RASTER_EXTS = recolor.RASTER_EXTS
ALLOWED = {".pdf", ".svg"} | RASTER_EXTS
HEX_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
BADGE = {"fill": "填充", "stroke": "描边", "fill+stroke": "填充+描边", "pixel": "像素"}


def norm(h):
    return (h or "").strip().lower()


def scan_colors(path, ext):
    if ext == ".svg":
        colors = recolor.scan_svg_colors(path.read_text(encoding="utf-8", errors="replace"))
        total = len(colors)
    elif ext == ".pdf":
        colors = recolor.scan_pdf_colors(str(path))
        total = len(colors)
    else:
        colors, total = recolor.scan_raster_colors(str(path), limit=800)
    items = sorted(colors.items(), key=lambda kv: -kv[1]["count"])
    out = []
    for rgb, info in items:
        fc = info.get("fill_count", 0)
        sc = info.get("stroke_count", 0)
        kind = "fill+stroke" if fc and sc else ("stroke" if sc else "fill")
        if ext in RASTER_EXTS:
            kind = "pixel"
        out.append(
            {
                "hex": recolor.rgb_to_hex(rgb),
                "count": info["count"],
                "kind": kind,
            }
        )
    return out[:300], total


def preview_np(path, max_w=1000):
    """渲染第一页/整图成 RGB numpy 数组（PDF/SVG 用 fitz，位图用 PIL）。"""
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


def map_preview(arr, table, smooth, anchors):
    stats = {"matched": 0}
    new = recolor.map_rgb_array(arr, table, 0, anchors if smooth else None, stats)
    return new, stats["matched"]


def highlight_image(arr, rgb, tol=140):
    out = arr.copy()
    cands = [rgb]
    for a in (0.8, 0.6, 0.4, 0.25, 0.12):
        cands.append([round(rgb[0] * a + 255 * (1 - a)), round(rgb[1] * a + 255 * (1 - a)), round(rgb[2] * a + 255 * (1 - a))])
    fa = out.astype(np.int16)
    best = np.full((arr.shape[0], arr.shape[1]), 10**9, dtype=np.int32)
    for c in cands:
        d = np.abs(fa[..., 0] - c[0]) + np.abs(fa[..., 1] - c[1]) + np.abs(fa[..., 2] - c[2])
        best = np.minimum(best, d)
    mask = best <= tol
    out[mask] = np.minimum(255, out[mask].astype(np.int16) + 80).astype(np.uint8)
    out[~mask] = (out[~mask].astype(np.float32) * 0.55).astype(np.uint8)
    return out


def build_table(mapping_hex):
    table = {}
    for k, v in mapping_hex.items():
        try:
            table[recolor.color_to_rgb(k)] = recolor.color_to_rgb(v)
        except ValueError:
            continue
    return table


def generate_file(sid, ext):
    d = SESSIONS / sid
    src = d / ("original" + ext)
    out = d / ("recolored" + ext)
    cfg = json.loads((d / "mapping.json").read_text(encoding="utf-8"))
    table = build_table(cfg["mapping"])
    stats = {"matched": 0}
    if ext == ".svg":
        text = src.read_text(encoding="utf-8", errors="replace")
        out.write_text(recolor.recolor_svg(text, table, stats), encoding="utf-8")
    elif ext in RASTER_EXTS:
        recolor.recolor_raster(str(src), table, str(out), stats, 0, None)
    else:
        recolor.apply_pdf(str(src), table, str(out), stats)
    return out


# ---------------------------------------------------------------- 事件处理

def on_upload(file):
    if file is None:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), "请先上传文件"
    path = Path(file.name)
    ext = path.suffix.lower()
    if ext not in ALLOWED:
        return gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), f"不支持 {ext}，请上传 SVG/PDF/图片"
    sid = uuid.uuid4().hex[:12]
    d = SESSIONS / sid
    d.mkdir()
    src = d / ("original" + ext)
    shutil.copyfile(str(path), str(src))
    items, total = scan_colors(src, ext)
    before = preview_np(src)
    bg = preview_background(before)
    rows = [[c["hex"], c["hex"], BADGE.get(c["kind"], c["kind"]), c["count"]] for c in items]
    choices = [c["hex"] for c in items]
    state = {"sid": sid, "ext": ext, "name": path.name, "before": before, "after": None, "bg": bg, "smooth": True}
    return (
        rows,
        before,
        gr.update(choices=choices),
        gr.update(value=None),
        state,
        f"扫描完成：共 {total} 种颜色（展示前 {len(rows)} 种），背景色 {bg}",
    )


def on_apply(state, df, protect, smooth):
    if not state:
        return gr.update(), "请先上传文件"
    table_hex = {}
    if df is not None and hasattr(df, "values"):
        for r in df.values.tolist():
            old = norm(str(r[0]))
            new = norm(str(r[1]))
            if old == new or not HEX_RE.match(old) or not HEX_RE.match(new):
                continue
            if protect and state.get("bg") and old == norm(state["bg"]):
                continue
            table_hex[old] = new
    if not table_hex:
        return gr.update(), "没有有效的颜色映射：请修改“替换色”列"
    anchors = None
    # 平滑模式锚点：直接从预览里取常见色
    if smooth:
        packed = (
            state["before"][..., 0].astype(np.uint32) << 16
            | state["before"][..., 1].astype(np.uint32) << 8
            | state["before"][..., 2].astype(np.uint32)
        )
        vals, counts = np.unique(packed, return_counts=True)
        order = np.argsort(-counts)[:10]
        anchors = [((int(vals[i]) >> 16) & 255, (int(vals[i]) >> 8) & 255, int(vals[i]) & 255) for i in order]
    after, matched = map_preview(state["before"], build_table(table_hex), smooth, anchors)
    (SESSIONS / state["sid"] / "mapping.json").write_text(
        json.dumps({"mapping": table_hex}, ensure_ascii=False), encoding="utf-8"
    )
    state["after"] = after
    return after, f"已应用：替换约 {matched} 像素（预览）。点“生成下载文件”得到最终文件"


def on_highlight(state, color, which):
    if not state or not color:
        return gr.update(), "请先上传文件并选择颜色"
    rgb = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    base = state["after"] if which == "换色后" and state.get("after") is not None else state["before"]
    return highlight_image(base, rgb), f"已高亮 {color}"


def on_download(state):
    if not state:
        return gr.update(), "请先上传文件"
    out = generate_file(state["sid"], state["ext"])
    return str(out), f"已生成：{out.name}"


def on_reset(state):
    if state:
        return state["before"], "已恢复原图"
    return gr.update(), ""


# ---------------------------------------------------------------- 界面

with gr.Blocks(title="一键换色") as demo:
    gr.Markdown(
        "# SVG / PDF / 图片一键换色\n"
        "上传文件 → 自动扫出颜色（标注填充/描边）→ 修改“替换色”列 → 应用预览 → 下载。"
    )
    state = gr.State(None)
    with gr.Row():
        with gr.Column(scale=1):
            file_in = gr.File(
                label="上传文件（可拖拽）",
                file_types=[".svg", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"],
            )
            colors_df = gr.Dataframe(
                headers=["原色", "替换色", "类型", "次数"],
                datatype=["str", "str", "str", "number"],
                interactive=True,
                label="颜色映射（改“替换色”列即可）",
            )
            protect = gr.Checkbox(value=True, label="不替换背景色")
            smooth = gr.Checkbox(value=True, label="平滑换色（处理渐变/抗锯齿边缘）")
            apply_btn = gr.Button("应用配色（预览）", variant="primary")
            hl_color = gr.Dropdown(label="定位颜色", choices=[])
            hl_which = gr.Radio(["原图", "换色后"], value="原图", label="高亮作用于")
            hl_btn = gr.Button("高亮该颜色")
            reset_btn = gr.Button("恢复原图")
            download_btn = gr.Button("生成下载文件", variant="primary")
            status = gr.Markdown()
        with gr.Column(scale=1):
            before = gr.Image(label="原图", interactive=False)
            after = gr.Image(label="换色后", interactive=False)
            file_out = gr.File(label="下载")

    file_in.upload(
        on_upload,
        inputs=[file_in],
        outputs=[colors_df, before, hl_color, after, state, status],
    )
    apply_btn.click(on_apply, inputs=[state, colors_df, protect, smooth], outputs=[after, status])
    hl_btn.click(on_highlight, inputs=[state, hl_color, hl_which], outputs=[before, status])
    reset_btn.click(on_reset, inputs=[state], outputs=[before, status])
    download_btn.click(on_download, inputs=[state], outputs=[file_out, status])


if __name__ == "__main__":
    demo.launch(theme=gr.themes.Soft())
