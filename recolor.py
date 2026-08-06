#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
recolor.py — SVG / PDF / PNG 等图片一键换色工具

用法:
  python recolor.py scan     <文件>                    # 列出文件用到的所有颜色(含透明度)
  python recolor.py template <文件> [-o mapping.json]  # 生成可编辑的颜色映射模板
  python recolor.py apply    <文件> <mapping.json> [-o 输出文件] [-t 容差] [-s]

透明度默认保持不变。映射文件格式(JSON):
  { "#e41a1c": "#00b8d9", "rgb(255,0,0)": "#4daf4a" }
键和值支持 #RGB / #RRGGBB / #RRGGBBAA / rgb(r,g,b) 写法。
位图(PNG/JPG/GIF/BMP/TIFF/WebP)加 -s 走平滑 LUT 换色(渐变/抗锯齿边缘自动过渡)。
"""

import argparse
import bisect
import json
import re
import shutil
import sys
from pathlib import Path

PDF_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
RASTER_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp"}


def eprint(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- 颜色解析

def parse_hex(h):
    h = h.strip().lstrip("#").lower()
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    if len(h) not in (6, 8):
        raise ValueError(f"无法识别的 hex 颜色: #{h}")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


def parse_css_num(s):
    s = s.strip()
    if s.endswith("%"):
        return round(float(s[:-1]) / 100 * 255)
    return round(float(s))


def parse_rgb_func(s):
    m = re.match(r"rgba?\((.*)\)$", s.strip(), re.I)
    if not m:
        raise ValueError(f"无法解析颜色: {s!r}")
    parts = [p for p in re.split(r"[,/]|\s+", m.group(1).strip()) if p]
    if len(parts) < 3:
        raise ValueError(f"颜色分量不足: {s!r}")
    return tuple(parse_css_num(p) for p in parts[:3])


def color_to_rgb(s):
    s = s.strip()
    if s.startswith("#"):
        return parse_hex(s)
    if s.lower().startswith("rgb"):
        return parse_rgb_func(s)
    raise ValueError(f"只支持 #hex 或 rgb(...) 形式: {s!r}")


def load_mapping(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    table = {}
    for k, v in raw.items():
        try:
            table[color_to_rgb(k)] = color_to_rgb(v)
        except ValueError as e:
            eprint(f"[跳过] {e}")
    return table


# ---------------------------------------------------------------- SVG

HEX_RE = re.compile(r"(?<!url\()#([0-9a-fA-F]{3,8})")
RGB_RE = re.compile(r"rgba?\((.*?)\)", re.I)


def recolor_svg(text, table, stats):
    def rep_hex(m):
        h = m.group(1).lower()
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) not in (6, 8):
            return m.group(0)
        rgb = tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
        if rgb in table:
            stats["matched"] += 1
            return "#" + rgb_to_hex(table[rgb])[1:] + h[6:]  # 保留 #RRGGBBAA 的 alpha
        return m.group(0)

    def rep_rgb(m):
        try:
            rgb = parse_rgb_func(m.group(0))
        except ValueError:
            return m.group(0)
        if rgb not in table:
            return m.group(0)
        inner = m.group(1)
        stats["matched"] += 1
        new = ", ".join(str(v) for v in table[rgb])
        seg = [p for p in re.split(r"[,/]|\s+", inner) if p]
        alpha = seg[3] if len(seg) == 4 else None
        if alpha is not None:
            if "," in inner:
                return f"rgb({new}, {alpha})"
            return f"rgb({new} / {alpha})"
        return f"rgb({new})"

    out = HEX_RE.sub(rep_hex, text)
    out = RGB_RE.sub(rep_rgb, out)
    return out


def scan_svg_colors(text):
    colors = {}

    def add(rgb, is_fill=True, n=1):
        e = colors.setdefault(
            rgb, {"count": 0, "opacities": set(), "fill_count": 0, "stroke_count": 0}
        )
        e["count"] += n
        if is_fill:
            e["fill_count"] += n
        else:
            e["stroke_count"] += n

    for m in HEX_RE.finditer(text):
        h = m.group(1).lower()
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        if len(h) in (6, 8):
            add(tuple(int(h[i : i + 2], 16) for i in (0, 2, 4)))
    for m in RGB_RE.finditer(text):
        try:
            add(parse_rgb_func(m.group(0)))
        except ValueError:
            pass

    # 填充/描边上下文（近似：fill/stop-color/color 视为填充，stroke 视为描边）
    def _ctx_add(token, is_fill):
        token = token.strip()
        try:
            rgb = color_to_rgb(token)
        except ValueError:
            return
        e = colors.setdefault(
            rgb, {"count": 0, "opacities": set(), "fill_count": 0, "stroke_count": 0}
        )
        if is_fill:
            e["fill_count"] += 1
        else:
            e["stroke_count"] += 1

    ctx_pat = r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))"
    for m in re.finditer(
        r"(?:fill|stop-color|color)\s*[:=]\s*" + ctx_pat, text, re.I
    ):
        _ctx_add(m.group(1), True)
    for m in re.finditer(r"stroke\s*[:=]\s*" + ctx_pat, text, re.I):
        _ctx_add(m.group(1), False)
    return colors


# ---------------------------------------------------------------- 位图 (PNG/JPG/GIF/BMP/TIFF/WebP)

def _unpack_rgb(p):
    return ((p >> 16) & 255, (p >> 8) & 255, p & 255)


def scan_raster_colors(path, limit=800, combo_cap=2000):
    """扫描位图颜色。返回 (colors, total_unique_rgb)，
    colors 只保留出现次数最多的 limit 种：{rgb: {"count": n, "opacities": {alpha...}}}。"""
    import numpy as np
    from PIL import Image

    arr = np.asarray(Image.open(path).convert("RGBA"))
    r = arr[..., 0].astype(np.uint32)
    g = arr[..., 1].astype(np.uint32)
    b = arr[..., 2].astype(np.uint32)
    a = arr[..., 3].astype(np.uint32)

    packed_rgb = (r << 16) | (g << 8) | b
    total_rgb = int(len(np.unique(packed_rgb)))

    # (rgb, alpha) 组合统计：同色不同透明度算多种组合，汇总时仍按 rgb 合并
    combo = (packed_rgb.astype(np.uint64) << 8) | a.astype(np.uint64)
    ucombo, ccombo = np.unique(combo, return_counts=True)
    order = np.argsort(-ccombo)[:combo_cap]

    colors = {}
    for i in order:
        p = int(ucombo[i])
        rgb = _unpack_rgb(p >> 8)
        alpha = (p & 255) / 255
        e = colors.setdefault(
            rgb, {"count": 0, "opacities": set(), "fill_count": 0, "stroke_count": 0}
        )
        e["count"] += int(ccombo[i])
        e["fill_count"] += int(ccombo[i])
        e["opacities"].add(round(alpha, 4))

    top = sorted(colors.items(), key=lambda kv: -kv[1]["count"])[:limit]
    return dict(top), total_rgb


def _recolor_raster_exact(rgb, table, tolerance, stats):
    """精确/容差匹配：先量化调色板，再按像素查表替换。"""
    import numpy as np

    r = rgb[..., 0].astype(np.uint32)
    g = rgb[..., 1].astype(np.uint32)
    b = rgb[..., 2].astype(np.uint32)
    packed = (r << 16) | (g << 8) | b
    uniq, inverse = np.unique(packed, return_inverse=True)
    new_pal = np.stack(
        [(uniq >> 16) & 255, (uniq >> 8) & 255, uniq & 255], axis=1
    ).astype(np.uint32)

    keys = np.array(sorted(table.keys()), dtype=np.int64)
    vals = np.array([table[k] for k in sorted(table.keys())], dtype=np.int64)
    touched = np.zeros(len(uniq), dtype=bool)

    if tolerance <= 0:
        key_packed = (keys[:, 0] << 16) | (keys[:, 1] << 8) | keys[:, 2]
        for kp, v in zip(key_packed, vals):
            i = int(np.searchsorted(uniq, kp))
            if i < len(uniq) and int(uniq[i]) == int(kp):
                new_pal[i] = v
                touched[i] = True
    else:
        tol = int(tolerance)
        for (kr, kg, kb), v in zip(keys, vals):
            dist = (
                np.abs(new_pal[:, 0].astype(np.int64) - kr)
                + np.abs(new_pal[:, 1].astype(np.int64) - kg)
                + np.abs(new_pal[:, 2].astype(np.int64) - kb)
            )
            mask = dist <= tol
            if mask.any():
                new_pal[mask] = v
                touched[mask] = True

    stats["matched"] = int(np.count_nonzero(touched[inverse]))
    out = np.stack([new_pal[inverse, 0], new_pal[inverse, 1], new_pal[inverse, 2]], axis=-1)
    return out.astype(np.uint8)


def _build_smooth_lut(sources, targets, ids, size=33, r_exact=24.0, seg_eps=24.0):
    """构建 3D LUT：锚点=映射色(带新色) + 未映射的常见色/黑白灰(保持原色)。
    锚点附近用反距离加权；锚点连线附近(抗锯齿/渐变)按线段参数插值；其余颜色不变。"""
    import numpy as np

    A_src, A_tgt = [sources], [targets]
    for c in ids:
        A_src.append(np.asarray(c, dtype=np.float32).reshape(1, 3))
        A_tgt.append(np.asarray(c, dtype=np.float32).reshape(1, 3))
    A_src = np.concatenate(A_src, axis=0)
    A_tgt = np.concatenate(A_tgt, axis=0)

    axis = np.linspace(0, 255, size, dtype=np.float32)
    R, G, B = np.meshgrid(axis, axis, axis, indexing="ij")
    C = np.stack([R.ravel(), G.ravel(), B.ravel()], axis=-1)
    D = np.sqrt(((C[:, None, :] - A_src[None, :, :]) ** 2).sum(-1))  # (N, m)

    m = A_src.shape[0]
    k = min(6, m)
    nearest = np.argpartition(D, kth=k - 1, axis=1)[:, :k]
    rows = np.arange(D.shape[0])
    d_min = D[rows[:, None], nearest].min(axis=1)

    out = C.copy()
    exact = d_min <= r_exact
    if exact.any():
        w = np.clip(1 - D[exact] / r_exact, 0, 1) ** 2
        s = w.sum(axis=1, keepdims=True)
        out[exact] = (w @ A_tgt) / np.maximum(s, 1e-6)

    rem = ~exact
    if rem.any() and k >= 2:
        A = A_src[nearest[rem]]  # (R, k, 3)
        T = A_tgt[nearest[rem]]
        Cv = C[rem]  # (R, 3)
        best = np.zeros_like(A[:, 0])
        best_perp = np.full(A.shape[0], np.inf)
        for i in range(k):
            for j in range(i + 1, k):
                ai = A[:, i]
                v = A[:, j] - ai
                len2 = (v * v).sum(-1, keepdims=True)
                t = ((Cv - ai) * v).sum(-1, keepdims=True) / np.maximum(len2, 1e-6)
                t = np.clip(t, 0.0, 1.0)
                P = ai + t * v
                perp2 = ((Cv - P) ** 2).sum(-1)
                cand = T[:, i] + t * (T[:, j] - T[:, i])
                better = perp2 < best_perp
                best = np.where(better[..., None], cand, best)
                best_perp = np.where(better, perp2, best_perp)
        mask = best_perp <= seg_eps * seg_eps
        out[rem] = np.where(mask[..., None], best, out[rem])

    return out.reshape(size, size, size, 3).astype(np.float32)


def _apply_lut(img, lut, chunk=512):
    """三线性插值应用 LUT（按行分块，控制内存）。"""
    import numpy as np

    n = lut.shape[0]
    scale = (n - 1) / 255.0
    h, w = img.shape[:2]
    out = np.empty((h, w, 3), dtype=np.float32)
    for s in range(0, h, chunk):
        e = min(h, s + chunk)
        f = img[s:e] * scale
        x0 = np.floor(f).astype(np.int32)
        x1 = np.minimum(x0 + 1, n - 1)
        tx = f - x0
        w0 = 1 - tx
        w1 = tx
        c000 = lut[x0[..., 0], x0[..., 1], x0[..., 2]]
        c100 = lut[x1[..., 0], x0[..., 1], x0[..., 2]]
        c010 = lut[x0[..., 0], x1[..., 1], x0[..., 2]]
        c001 = lut[x0[..., 0], x0[..., 1], x1[..., 2]]
        c110 = lut[x1[..., 0], x1[..., 1], x0[..., 2]]
        c101 = lut[x1[..., 0], x0[..., 1], x1[..., 2]]
        c011 = lut[x0[..., 0], x1[..., 1], x1[..., 2]]
        c111 = lut[x1[..., 0], x1[..., 1], x1[..., 2]]
        a = w0[..., 0:1] * w0[..., 1:2] * w0[..., 2:3]
        b = w1[..., 0:1] * w0[..., 1:2] * w0[..., 2:3]
        cc = w0[..., 0:1] * w1[..., 1:2] * w0[..., 2:3]
        d = w0[..., 0:1] * w0[..., 1:2] * w1[..., 2:3]
        e2 = w1[..., 0:1] * w1[..., 1:2] * w0[..., 2:3]
        ff = w1[..., 0:1] * w0[..., 1:2] * w1[..., 2:3]
        g = w0[..., 0:1] * w1[..., 1:2] * w1[..., 2:3]
        hh = w1[..., 0:1] * w1[..., 1:2] * w1[..., 2:3]
        out[s:e] = (
            c000 * a + c100 * b + c010 * cc + c001 * d
            + c110 * e2 + c101 * ff + c011 * g + c111 * hh
        )
    return out


def map_rgb_array(rgb, table, tolerance=0, anchors=None, stats=None):
    """对 RGB 数组应用换色映射。anchors 给定则走平滑 LUT，否则精确/容差匹配。"""
    import numpy as np

    stats = stats if stats is not None else {"matched": 0}
    if anchors is not None:
        mapped = set(table.keys())
        ids = [tuple(c) for c in anchors if tuple(c) not in mapped][:8]
        for c in ((255, 255, 255), (0, 0, 0), (128, 128, 128)):
            if c not in mapped and c not in ids and len(ids) < 10:
                ids.append(c)
        sources = np.array(sorted(table.keys()), dtype=np.float32)
        targets = np.array([table[k] for k in sorted(table.keys())], dtype=np.float32)
        lut = _build_smooth_lut(sources, targets, ids)
        new_rgb = _apply_lut(rgb.astype(np.float32), lut)
        new_rgb = new_rgb.round().clip(0, 255).astype(np.uint8)
        # 纯色精确命中（保证和精确模式一致）
        for src, tgt in table.items():
            s = np.array(src, dtype=np.uint8)
            mask = (rgb == s).all(-1)
            if mask.any():
                new_rgb[mask] = tgt
        stats["matched"] = int(
            np.count_nonzero(
                np.abs(new_rgb.astype(np.int16) - rgb.astype(np.int16)).max(-1) > 1
            )
        )
        return new_rgb
    return _recolor_raster_exact(rgb, table, tolerance, stats)


def recolor_raster(path, table, outpath, stats, tolerance=0, anchors=None):
    """像素级换色。
    anchors=None: 精确/容差匹配（老逻辑）；
    anchors 给定: 平滑 LUT 换色，纯色仍精确映射，渐变/抗锯齿边缘自动平滑过渡。"""
    import numpy as np
    from PIL import Image

    arr = np.asarray(Image.open(path).convert("RGBA"))
    alpha = arr[..., 3]
    new_rgb = map_rgb_array(arr[..., :3], table, tolerance, anchors, stats)

    out = np.empty_like(arr)
    out[..., :3] = new_rgb
    out[..., 3] = alpha
    out_img = Image.fromarray(out, "RGBA")
    ext = Path(outpath).suffix.lower()
    if ext in (".jpg", ".jpeg", ".bmp", ".gif"):
        out_img = out_img.convert("RGB")
    out_img.save(outpath)


# ---------------------------------------------------------------- PDF

PDF_NUM_TOK = re.compile(PDF_NUM + r"\Z")
FILL_OPS = {"rg", "g", "k", "sc", "scn"}
UNSUPPORTED_CS = {"Pattern", "Indexed", "DeviceN", "Separation", "Lab"}


def _mask_spans(text):
    """内联图像与字符串字面量区间，换色时跳过。
    只在 '(' / 'BI' 出现位置附近做局部正则，避免对几百 MB 的流做全量扫描。"""
    if "(" not in text and "BI" not in text:
        return []
    spans = []
    # 内联图像 BI ... ID ... EI
    pos = 0
    while True:
        i = text.find("BI", pos)
        if i < 0:
            break
        prev_ok = i == 0 or not (text[i - 1].isalnum())
        next_ok = i + 2 >= len(text) or not (text[i + 2].isalnum())
        if prev_ok and next_ok:
            m = re.search(r"\bID\b.*?\bEI\b", text[i + 2 :], re.S)
            if m:
                spans.append((i, i + 2 + m.end()))
                pos = i + 2 + m.end()
                continue
        pos = i + 2
    # 字符串字面量 ( ... )
    pos = 0
    while True:
        i = text.find("(", pos)
        if i < 0:
            break
        m = re.match(r"\((?:\\.|[^\\()])*\)", text[i : i + 20000])
        if m:
            spans.append((i, i + m.end()))
            pos = i + m.end()
        else:
            pos = i + 1
    return spans


def _cmyk_to_rgb(c, mm, y, k):
    return tuple(round(255 * (1 - v) * (1 - k)) for v in (c, mm, y))


def _color_from_nums(vals, op, cs):
    """从紧邻颜色算子的数值中还原 RGB(0-255)。"""
    if not vals:
        return None
    if op in ("rg", "RG"):
        return tuple(round(v * 255) for v in vals[-3:]) if len(vals) >= 3 else None
    if op in ("g", "G"):
        v = round(vals[-1] * 255)
        return (v, v, v)
    if op in ("k", "K"):
        return _cmyk_to_rgb(*vals[-4:]) if len(vals) >= 4 else None
    # sc / SC / scn / SCN —— 按当前色彩空间判断
    if cs in ("DeviceRGB", "CalRGB"):
        return tuple(round(v * 255) for v in vals[-3:]) if len(vals) >= 3 else None
    if cs == "DeviceCMYK":
        return _cmyk_to_rgb(*vals[-4:]) if len(vals) >= 4 else None
    if cs in ("DeviceGray", "CalGray"):
        v = round(vals[-1] * 255)
        return (v, v, v)
    if cs in UNSUPPORTED_CS:
        return None
    # 未识别/命名色彩空间：按分量数回退（绝大多数绘图 PDF 是 RGB/CMYK/Gray）
    if len(vals) == 3:
        return tuple(round(v * 255) for v in vals[-3:])
    if len(vals) == 4:
        return _cmyk_to_rgb(*vals[-4:])
    if len(vals) == 1:
        v = round(vals[-1] * 255)
        return (v, v, v)
    return None


def _find_operator_positions(data):
    """用 numpy 批量定位内容流算子，返回按位置升序的 (pos, op) 数组。
    比逐 token 的 Python 循环快一个量级，适合几百 MB 的流。"""
    import numpy as np

    buf = np.frombuffer(data, dtype=np.uint8)
    n = buf.size
    ops = ("rg", "RG", "scn", "SCN", "sc", "SC", "cs", "CS", "gs", "g", "G", "k", "K")
    letters = np.frombuffer(
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", dtype=np.uint8
    )
    # 单次扫描所有算子的首字节，避免对每个算子都全量扫一遍
    first_vals = np.array([ord(c) for c in sorted({op[0] for op in ops})], dtype=np.uint8)
    cands = np.flatnonzero(np.isin(buf, first_vals))
    by_first = {}
    for v in first_vals.tolist():
        by_first[v] = cands[buf[cands] == v]

    pos_list, op_list = [], []
    for op in ops:
        ob = op.encode("latin-1")
        L = len(ob)
        first = by_first.get(ob[0])
        if first is None or first.size == 0:
            continue
        if L > 1:
            idx = first[first + L <= n]
            if idx.size == 0:
                continue
            seg = buf[idx[:, None] + np.arange(L)]
            idx = idx[np.all(seg == np.frombuffer(ob, dtype=np.uint8)[None, :], axis=1)]
            if idx.size == 0:
                continue
        else:
            idx = first
        # 边界：前后不能是字母（避免 g 命中 gs/rg，sc 命中 scn 等）
        follow = np.full(idx.size, 0, dtype=np.uint8)
        okf = idx + L < n
        follow[okf] = buf[np.minimum(idx[okf] + L, n - 1)]
        prev = np.full(idx.size, 0, dtype=np.uint8)
        okp = idx > 0
        prev[okp] = buf[np.maximum(idx[okp] - 1, 0)]
        keep = ~np.isin(follow, letters) & ~np.isin(prev, letters)
        idx = idx[keep]
        if idx.size:
            pos_list.append(idx)
            op_list.append(np.full(idx.size, op))
    if not pos_list:
        return np.array([], dtype=np.int64), np.array([], dtype=object)
    pos = np.concatenate(pos_list)
    opa = np.concatenate(op_list)
    order = np.argsort(pos, kind="stable")
    return pos[order], opa[order]


def _trailing_number_span(text, pos):
    """返回 (起始位置, 数值token列表)：紧邻 pos 之前的一串数值。"""
    i = pos
    toks = []
    first = pos
    while i > 0 and text[i - 1] in " \t\r\n":
        i -= 1
    while i > 0:
        j = i
        while j > 0 and text[j - 1] not in " \t\r\n":
            j -= 1
        tok = text[j:i]
        if not PDF_NUM_TOK.match(tok):
            break
        toks.append(tok)
        first = j
        if len(toks) >= 8:
            break
        i = j
        while i > 0 and text[i - 1] in " \t\r\n":
            i -= 1
    toks.reverse()
    return first, toks


def _preceding_name(text, pos):
    seg = text[max(0, pos - 48) : pos]
    toks = seg.split()
    if toks and toks[-1].startswith("/"):
        return toks[-1][1:]
    return None


def _covered(pos, starts, spans):
    i = bisect.bisect_right(starts, pos) - 1
    return i >= 0 and pos < spans[i][1]


def _canonical_cs(name):
    if name in ("DeviceRGB", "CalRGB"):
        return "DeviceRGB"
    if name == "DeviceCMYK":
        return "DeviceCMYK"
    if name in ("DeviceGray", "CalGray"):
        return "DeviceGray"
    return name


def _cs_base_from_obj(obj):
    m = re.search(r"/Alternate\s+/([A-Za-z0-9_.-]+)", obj)
    if m:
        return _canonical_cs(m.group(1))
    m = re.match(r"\s*\[/ICCBased\s+(\d+)", obj)
    if m:
        return "DeviceRGB" if int(m.group(1)) == 3 else "DeviceCMYK"
    m = re.search(r"\[/(CalRGB|CalGray|DeviceRGB|DeviceCMYK|DeviceGray)", obj)
    if m:
        return _canonical_cs(m.group(1))
    return None


def _color_space_map(doc, owner_xref):
    """解析 owner(页面/Form) 的 /Resources/ColorSpace，返回 {名称: 基础空间}。"""
    res = {}
    try:
        kind, val = doc.xref_get_key(owner_xref, "Resources/ColorSpace")
    except Exception:
        return res
    if kind == "xref":
        try:
            text = doc.xref_object(int(val.split()[0]))
        except Exception:
            return res
    elif kind == "dict":
        text = val
    else:
        return res
    for m in re.finditer(
        r"/([A-Za-z0-9_.-]+)\s+(/\[[^\]]*\]|/[A-Za-z0-9_.-]+|\d+\s+\d+\s+R)", text
    ):
        name, v = m.group(1), m.group(2).strip()
        if v.startswith("/"):
            base = _canonical_cs(v[1:])
        else:
            mm = re.match(r"(\d+)\s+\d+\s+R", v)
            if not mm:
                continue
            try:
                obj = doc.xref_object(int(mm.group(1)))
            except Exception:
                continue
            base = _cs_base_from_obj(obj)
        if base and base not in UNSUPPORTED_CS:
            res[name] = base
    return res


def _extgstate(doc, owner_xref):
    """解析 owner(页面/Form) 的 /Resources/ExtGState，返回 {名称: (fill_alpha, stroke_alpha)}。"""
    res = {}
    try:
        kind, val = doc.xref_get_key(owner_xref, "Resources/ExtGState")
    except Exception:
        return res
    if kind == "xref":
        try:
            text = doc.xref_object(int(val.split()[0]))
        except Exception:
            return res
    elif kind == "dict":
        text = val
    else:
        return res
    for m in re.finditer(r"/([A-Za-z0-9_.-]+)\s*<<(.*?)>>", text, re.S):
        body = m.group(2)
        ca = re.search(r"/ca\s+([0-9.]+)", body)
        CA = re.search(r"/CA\s+([0-9.]+)", body)
        res[m.group(1)] = (
            float(ca.group(1)) if ca else 1.0,
            float(CA.group(1)) if CA else 1.0,
        )
    return res


def _page_units(doc):
    """收集 (xref, 数据, 资源owner_xref)：页面内容流 + 页面级 Form XObject。"""
    units = []
    for page in doc:
        for xref in page.get_contents():
            data = doc.xref_stream(xref)
            if data:
                units.append((xref, data, page.xref))
        try:
            for xref in page.get_xobjects().values():
                try:
                    if doc.xref_get_key(xref, "Subtype")[1] != "/Form":
                        continue
                except Exception:
                    continue
                data = doc.xref_stream(xref)
                if data:
                    units.append((xref, data, xref))
        except Exception:
            pass
    return units


def scan_stream_colors(data, extg=None, cs_names=None):
    """扫描内容流颜色（numpy 定位算子 + 回看数值）。返回 [(rgb, alpha), ...]。"""
    extg = extg or {}
    cs_names = cs_names or {}
    text = data.decode("latin-1")
    out = []
    state = {"fill_cs": None, "stroke_cs": None, "fill_a": 1.0, "stroke_a": 1.0}
    pos, ops = _find_operator_positions(data)
    for p, op in zip(pos.tolist(), ops.tolist()):
        if op in ("cs", "CS"):
            name = _preceding_name(text, p)
            if name:
                state["fill_cs" if op == "cs" else "stroke_cs"] = cs_names.get(name, name)
            continue
        if op == "gs":
            name = _preceding_name(text, p)
            if name:
                a = extg.get(name)
                if a:
                    state["fill_a"], state["stroke_a"] = a
            continue
        _, toks = _trailing_number_span(text, p)
        if not toks:
            continue
        try:
            vals = [float(t) for t in toks]
        except ValueError:
            continue
        is_fill = op in FILL_OPS
        rgb = _color_from_nums(vals, op, state["fill_cs" if is_fill else "stroke_cs"])
        if rgb is not None:
            out.append((rgb, state["fill_a" if is_fill else "stroke_a"], is_fill))
    return out


def scan_pdf_colors(path, on_progress=None):
    """扫描 PDF 颜色（含命名色彩空间与 Form XObject）。"""
    import fitz

    doc = fitz.open(path)
    colors = {}

    def add(rgb, op=None, n=1):
        e = colors.setdefault(
            rgb, {"count": 0, "opacities": set(), "fill_count": 0, "stroke_count": 0}
        )
        e["count"] += n
        if op is not None:
            e["opacities"].add(round(op, 4))

    units = []
    for _xref, data, owner in _page_units(doc):
        units.append((data, _extgstate(doc, owner), _color_space_map(doc, owner)))
    total = len(units)
    for i, (data, extg, csn) in enumerate(units):
        if on_progress:
            on_progress(i, total)
        for rgb, op, is_fill in scan_stream_colors(data, extg, csn):
            add(rgb, op)
            if is_fill:
                colors[rgb]["fill_count"] += 1
            else:
                colors[rgb]["stroke_count"] += 1
    if on_progress:
        on_progress(total, total)
    doc.close()
    return colors


def recolor_pdf_stream(data, table, stats, warned, cs_names=None):
    """内容流换色：定位算子 → 回看数值 → 倒序替换（保留原文件字节）。"""
    cs_names = cs_names or {}
    text = data.decode("latin-1")
    spans = sorted(_mask_spans(text))
    starts = [a for a, _ in spans]
    state = {"fill_cs": None, "stroke_cs": None}
    edits = []
    pos, ops = _find_operator_positions(data)
    for p, op in zip(pos.tolist(), ops.tolist()):
        if _covered(p, starts, spans):
            continue
        if op in ("cs", "CS"):
            name = _preceding_name(text, p)
            if name:
                state["fill_cs" if op == "cs" else "stroke_cs"] = cs_names.get(name, name)
            continue
        if op == "gs":
            continue
        start, toks = _trailing_number_span(text, p)
        if not toks:
            continue
        try:
            vals = [float(t) for t in toks]
        except ValueError:
            continue
        is_fill = op in FILL_OPS
        cs = state["fill_cs" if is_fill else "stroke_cs"]
        rgb = _color_from_nums(vals, op, cs)
        if rgb is None:
            if cs and cs not in UNSUPPORTED_CS and cs not in warned:
                warned.add(cs)
                eprint(f"[警告] 色彩空间 {cs} 的 sc/SCN 未替换")
            continue
        if rgb in table:
            r, g, b = table[rgb]
            new_op = "rg" if is_fill else "RG"
            repl = f"{r/255:.6f} {g/255:.6f} {b/255:.6f} {new_op}"
            edits.append((start, p + len(op), repl))
            stats["matched"] += 1

    if not edits:
        return data
    # 单次拼接，避免每处编辑都复制整个字符串（几十万处编辑时是数量级差异）
    edits.sort()
    parts = []
    pos = 0
    for start, end, repl in edits:
        parts.append(text[pos:start])
        parts.append(repl)
        pos = end
    parts.append(text[pos:])
    return "".join(parts).encode("latin-1")


def apply_pdf(inpath, table, outpath, stats, on_progress=None):
    import fitz

    doc = fitz.open(inpath)
    warned = set()
    changes = {}
    units = _page_units(doc)
    total = len(units)
    for i, (xref, data, owner) in enumerate(units):
        if on_progress:
            on_progress(i, total)
        if b" sh" in data and re.search(rb"\b[A-Za-z0-9_.-]+\s+sh\b", data):
            eprint(f"[警告] 内容流 {owner} 含渐变/着色对象(shading)，其内部颜色未替换")
        csn = _color_space_map(doc, owner)
        new_data = recolor_pdf_stream(data, table, stats, warned, csn)
        if new_data != data:
            changes[xref] = new_data
    doc.close()

    if not changes:
        shutil.copyfile(inpath, outpath)
        return

    # 快路径：pikepdf 只重写有改动的流，zlib level 1 预压缩，其余流原样保留
    try:
        import pikepdf
    except ImportError:
        pikepdf = None
    if pikepdf is not None:
        try:
            _apply_pdf_pikepdf(inpath, outpath, changes)
            return
        except Exception as e:
            eprint(f"[警告] pikepdf 保存失败，回退 fitz: {e}")

    # 兜底：fitz 慢路径
    doc = fitz.open(inpath)
    for xref, new_data in changes.items():
        doc.update_stream(xref, new_data)
    doc.save(outpath, garbage=0, deflate=True)
    doc.close()


def _apply_pdf_pikepdf(inpath, outpath, changes):
    """pikepdf 快路径：预压缩(level 1)改动流，未改动对象原样保留。"""
    import zlib

    import pikepdf

    pdf = pikepdf.open(inpath)
    for xref, new_data in changes.items():
        st = pdf.get_object((xref, 0))
        if not isinstance(st, pikepdf.Stream):
            raise RuntimeError(f"xref {xref} 不是流对象")
        # 小流用高压缩(体积优先)，大流用快速压缩(速度优先，体积仅大~20%)
        level = 6 if len(new_data) < 10 * 1024 * 1024 else 1
        st.write(zlib.compress(new_data, level))
        st.stream_dict["/Filter"] = pikepdf.Name("/FlateDecode")
        if "/DecodeParms" in st.stream_dict:
            del st.stream_dict["/DecodeParms"]
    pdf.save(outpath, compress_streams=False)
    pdf.close()


# ---------------------------------------------------------------- 输出/命令

def print_colors(colors):
    items = sorted(colors.items(), key=lambda kv: -kv[1]["count"])
    print(f"共发现 {len(items)} 种颜色:")
    for i, (rgb, info) in enumerate(items, 1):
        ops = "、".join(f"{o:g}" for o in sorted(info["opacities"])) if info["opacities"] else "-"
        print(f"  {i:>3}. {rgb_to_hex(rgb)}  (rgb{tuple(rgb)})  使用 {info['count']} 次, 透明度 {ops}")


def cmd_scan(args):
    src = Path(args.input)
    suffix = src.suffix.lower()
    if suffix == ".svg":
        text = src.read_text(encoding="utf-8", errors="replace")
        print_colors(scan_svg_colors(text))
    elif suffix == ".pdf":
        print_colors(scan_pdf_colors(str(src)))
    elif suffix in RASTER_EXTS:
        colors, total = scan_raster_colors(str(src))
        print(f"图片共 {total} 种颜色，展示出现次数最多的 {len(colors)} 种:")
        print_colors(colors)
    else:
        eprint("只支持 .svg / .pdf / PNG / JPG / GIF / BMP / TIFF / WebP")
        return 1
    return 0


def cmd_template(args):
    src = Path(args.input)
    suffix = src.suffix.lower()
    if suffix == ".svg":
        colors = scan_svg_colors(src.read_text(encoding="utf-8", errors="replace"))
    elif suffix == ".pdf":
        colors = scan_pdf_colors(str(src))
    elif suffix in RASTER_EXTS:
        colors, _ = scan_raster_colors(str(src))
    else:
        eprint("只支持 .svg / .pdf / PNG / JPG / GIF / BMP / TIFF / WebP")
        return 1
    items = sorted(colors.items(), key=lambda kv: -kv[1]["count"])
    mapping = {rgb_to_hex(rgb): rgb_to_hex(rgb) for rgb, _ in items}
    out = Path(args.output) if args.output else Path("mapping.json")
    out.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    print(f"已生成 {len(mapping)} 条颜色映射 -> {out}  (把等号右边的值改成想要的新颜色)")
    return 0


def cmd_apply(args):
    src = Path(args.input)
    mapping = load_mapping(args.mapping)
    if not mapping:
        eprint("映射为空，无法换色")
        return 1
    out = Path(args.output) if args.output else src.with_name(src.stem + ".recolored" + src.suffix)
    stats = {"matched": 0}
    suffix = src.suffix.lower()
    if suffix == ".svg":
        try:
            text = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = src.read_text(encoding="latin-1")
        out.write_text(recolor_svg(text, mapping, stats), encoding="utf-8")
    elif suffix in RASTER_EXTS:
        anchors = None
        if args.smooth:
            cols, _ = scan_raster_colors(str(src))
            anchors = list(cols.keys())
        recolor_raster(str(src), mapping, str(out), stats, args.tolerance, anchors)
    elif suffix == ".pdf":
        apply_pdf(str(src), mapping, str(out), stats)
    else:
        eprint("只支持 .svg / .pdf / PNG / JPG / GIF / BMP / TIFF / WebP")
        return 1
    print(f"替换 {stats['matched']} 处颜色 -> {out}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="SVG / PDF / 图片一键换色工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="列出文件用到的所有颜色")
    p_scan.add_argument("input")
    p_scan.set_defaults(func=cmd_scan)

    p_tpl = sub.add_parser("template", help="生成颜色映射模板")
    p_tpl.add_argument("input")
    p_tpl.add_argument("-o", "--output", help="输出映射文件(默认 mapping.json)")
    p_tpl.set_defaults(func=cmd_template)

    p_apply = sub.add_parser("apply", help="按映射文件一键换色")
    p_apply.add_argument("input")
    p_apply.add_argument("mapping", help="JSON 映射文件")
    p_apply.add_argument("-o", "--output", help="输出文件(默认 原名.recolored.后缀)")
    p_apply.add_argument("-t", "--tolerance", type=int, default=0,
                         help="近似色容差 0-255(默认 0=精确匹配，JPG/抗锯齿边缘可调大)")
    p_apply.add_argument("-s", "--smooth", action="store_true",
                         help="平滑换色：纯色精确映射，渐变/抗锯齿边缘自动平滑过渡(位图)")
    p_apply.set_defaults(func=cmd_apply)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
