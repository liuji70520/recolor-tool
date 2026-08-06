#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hugging Face Spaces 入口。

直接托管 Flask 网页版（web/），与本地版功能完全一致：
三栏布局（主图预览 + 小文件上传 + 颜色映射表）、色块/颜色码点击高亮、
异步扫描进度、PDF 懒加载生成、导出 PDF/SVG/PNG/颜色代码（CSV/JSON）等。
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# 云端部署：监听所有网卡、不自动打开浏览器
os.environ.setdefault("AUTO_OPEN", "0")
os.environ.setdefault("HOST", "0.0.0.0")

from web.app import app  # noqa: E402


if __name__ == "__main__":
    port = int(
        os.environ.get("PORT")
        or os.environ.get("GRADIO_SERVER_PORT")
        or "7860"
    )
    app.run(host=os.environ.get("HOST", "0.0.0.0"), port=port, threaded=True)
