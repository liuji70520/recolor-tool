#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hugging Face Spaces 入口。

直接托管 Flask 网页版（web/），与本地版功能完全一致：
三栏布局（主图预览 + 小文件上传 + 颜色映射表）、色块/颜色码点击高亮、
异步扫描进度、PDF 懒加载生成、导出 PDF/SVG/PNG/颜色代码（CSV/JSON）等。
"""

import os
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# 云端部署：监听所有网卡、不自动打开浏览器
os.environ.setdefault("AUTO_OPEN", "0")
os.environ.setdefault("HOST", "0.0.0.0")


def _pick_port():
    """HF 不同 SDK 运行时给的端口变量不一样，逐个兼容。"""
    for name in ("PORT", "GRADIO_SERVER_PORT"):
        v = os.environ.get(name)
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 7860


def main():
    from web.app import app  # noqa: E402

    port = _pick_port()
    print(f"[recolor-tool] 启动 Flask 网页版，端口 {port}", flush=True)
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=port, threaded=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("[recolor-tool] 启动失败：\n" + traceback.format_exc(), flush=True)
        raise
