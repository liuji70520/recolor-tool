---
title: 一键换色 SVG/PDF/图片
emoji: 🎨
colorFrom: indigo
colorTo: pink
sdk: gradio
pinned: false
---

# recolor-tool — SVG / PDF 一键换色

适用场景：大数据图重新跑一次要很久，你只想快速试几套配色。直接对导出的
`PDF` / `SVG` 文件换色，秒出结果，不用重跑流程。

## 快速使用（二选一）

### 方式一：下载压缩包（Windows，免安装）

[![下载 RecolorTool v1.8.0](https://raw.githubusercontent.com/liuji70520/recolor-tool/main/docs/btn-download.svg)](https://github.com/liuji70520/recolor-tool/releases/latest/download/RecolorTool-v1.8.0.zip)

最新版压缩包（约 46MB，内含单文件 `RecolorTool.exe` + 使用说明）：

直接下载：

```bash
curl -L -o RecolorTool.zip https://github.com/liuji70520/recolor-tool/releases/latest/download/RecolorTool-v1.8.0.zip
```

> 注：仓库已公开，任何人都可直接运行上面的命令下载，无需登录。

解压后双击 `RecolorTool.exe`，浏览器自动打开即可使用（单文件自解压运行，
放在任意目录都能跑，无需安装 Python）。

### 方式二：网页版（在线使用，无需下载）

[![打开网页版](https://raw.githubusercontent.com/liuji70520/recolor-tool/main/docs/btn-web.svg)](https://liu-ji-recolor-tool.hf.space/)

打开 **https://liu-ji-recolor-tool.hf.space/** 即可使用。

两种方式功能一致：上传 SVG / PDF / 图片 → 自动扫色 → 颜色映射表
（点击颜色列即高亮）→ 应用配色实时预览 → 导出 PDF / SVG / PNG / 颜色代码
（CSV / JSON）。

## 本地网页版（Flask）

图形界面，支持拖拽上传、自动扫色、颜色映射表、前后预览对比、一键下载：

- 导出格式可选：**PDF / SVG / PNG / 颜色代码（CSV / JSON）**，PDF/SVG 之间可互转
  （PDF 导出 PNG/SVG 渲染的是换色后的矢量文件，PNG 导出可加 `?scale=` 调清晰度）
- 颜色定位高亮：按文件中的真实透明度精确匹配，只高亮该颜色本身，不误伤相近色

```bash
cd recolor-tool/web
python app.py
```

然后浏览器打开 `http://127.0.0.1:8377` 即可。默认只允许本机访问；
想让局域网内其他人也能用，把 `app.py` 里的 `host="127.0.0.1"` 改成
`host="0.0.0.0"`（部署到服务器同理）。启动后会自动打开浏览器；
8377 被占用时会自动换空闲端口（可用环境变量 `PORT` 指定，如 `PORT=9000`，
`AUTO_OPEN=0` 可关闭自动开浏览器）。

## 打包成 exe（发给别人）

```powershell
.\build_exe_pyinstaller.ps1
```

产物是单个 `dist\RecolorTool.exe`（约 46MB），把这个文件发给对方即可：

- 双击 `RecolorTool.exe` 即自动启动服务并打开浏览器（单文件自解压运行，
  从任何目录启动都行，无需解压安装）
- 端口被占用会自动换空闲端口；会话保存在 `%LOCALAPPDATA%\RecolorTool\sessions`
  （7 天自动清理）
- 不想要了直接删除 exe 即可

打包脚本里的 Python 路径是 `D:\envs\py312\python.exe`，换机器打包时改成自己的
环境即可（需安装 `pip install flask pymupdf pikepdf numpy pillow pyinstaller`；
构建环境里若装有 gradio/pandas，脚本会自动排除，不影响体积）。

## 三步工作流

```bash
# 1) 自动列出文件里用到的所有颜色（PDF 还会显示透明度）
python recolor.py scan figure.pdf

# 2) 生成颜色映射模板（等号右边改成你想要的新颜色即可）
python recolor.py template figure.pdf -o mapping.json

# 3) 一键换色，透明度默认不变
python recolor.py apply figure.pdf mapping.json -o figure-new.pdf
```

`mapping.json` 长这样（键 = 原颜色，值 = 新颜色）：

```json
{
  "#e41a1c": "#00b8d9",
  "#377eb8": "#f28500",
  "rgb(255, 0, 0)": "#4daf4a"
}
```

颜色写法支持 `#RGB`、`#RRGGBB`、`#RRGGBBAA`（8 位时只换 RGB、保留 alpha）、
`rgb(r,g,b)` 等。透明度通过文件里的 opacity / ExtGState 保存，换色不碰它们。

## 位图（PNG / JPG / GIF / BMP / TIFF / WebP）

位图默认走**平滑换色**：纯色精确映射，渐变 / 抗锯齿边缘通过 3D LUT 插值自动
平滑过渡（未映射的颜色保持不变），适合 PNG 导出的大数据图；JPG 这类有损格式
也能直接处理。需要严格只换纯色时，在网页里切到“仅精确匹配”，或 CLI 不加
`-s` 参数即可。

CLI 示例：

```bash
python recolor.py apply figure.png mapping.json -s -o figure-new.png   # 平滑
python recolor.py apply figure.jpg mapping.json -o figure-new.jpg      # 精确
```

## 已知限制

- PDF 里的**渐变（shading）**内部颜色暂不替换，工具会给出警告（matplotlib 的
  折线 / 散点 / pcolormesh 平铺填充都没问题）。
- PDF 内嵌**位图图片**不换色。
- 只有完全相同的颜色才会被替换（按 0–255 量化匹配），同色系但数值不同的颜色
  不会被波及。
- SVG 里的 CSS 颜色名（如 `red`）需要写成 `rgb(255,0,0)` 才能识别。

## 大文件优化

- PDF 扫描用 numpy 批量定位内容流算子（不再逐路径解析），几百 MB 的大图也能在
  十秒内扫完；支持 `/sRGB` 这类命名/ICC 色彩空间和页面 Form XObject。
- 上传、解析、换色都是异步任务，界面不阻塞；解析与换色过程显示实时进度
  （如“扫描内容流 3/10”），上传自带进度条。
- 位图换色用 numpy 向量化 + LUT 插值，2000×1200 的图约 2 秒。
- PDF 换色用 pikepdf(qpdf) 只重写有改动的流，zlib level 1 预压缩，未改动内容
  原样保留：120MB / 453MB 解压流的大图从约 3 分钟降到约 15 秒，输出仅大 20% 左右。
  依赖：`pip install pikepdf`（网页版已写入 requirements.txt；没有它会自动回退）。
  小流(<10MB)仍用高压缩，输出大小基本不变；只有超大流才降级为快速压缩。
- PDF 采用“应用配色秒出预览、下载时才生成真文件”的懒加载架构：反复试配色只花
  1~2 秒（像素级预览），点“下载结果”才做一次矢量换色+压缩，之后下载走缓存。

## 颜色定位高亮

网页版里点击颜色映射行（色块或颜色码），图中对应颜色的区域会变亮、其余区域
变暗，方便确认每个映射颜色在图上落在哪里；再点一次或点“取消高亮”恢复。

颜色映射表还标注了每个颜色的**类型**：填充 / 描边 / 填充+描边（位图为“像素”）。
文字、轴线、引线这类装饰元素基本都是描边，鼠标悬停徽标可查看 填充×N 描边×N
的明细，方便判断哪些是图的主要元素、哪些可以直接删掉不映射。

## 部署

注意：GitHub Pages 只能托管静态页面，跑不了本工具的 Python 后端。
推荐用 Hugging Face Spaces（免费 CPU 档 16GB 内存，能处理超大 PDF）。
空间 SDK 选择 **Gradio**（仓库根目录的 `app.py` 就是 Gradio 版界面，兼容
ZeroGPU）。

### 步骤（Hugging Face Spaces，推荐）

1. 在 GitHub 上新建仓库（如 `recolor-tool`），把本目录内容推上去；
2. 打开 https://huggingface.co/new-space ，创建 Space：SDK 选 **Gradio**，
   并把 GitHub 仓库连进来（或直接用 Git 推送）；
3. 等自动构建完成，打开生成的 `https://你的用户名-recolor-tool.hf.space` 即可使用；
4. 可设置环境变量：`MAX_UPLOAD_MB`（上传上限，默认 500）、`HISTORY_DAYS`（默认 7）。

Gradio 版（`app.py`）界面贴近 Flask 网页版：主图预览占主要区域、小文件上传、
颜色映射表带**色块**、点击“原颜色 / 替换颜色”列即高亮对应颜色、应用配色自动
刷新预览，支持导出 PDF / SVG / PNG / 颜色代码（CSV / JSON）和 PDF 懒加载生成。
本地 Flask 版（`web/`）功能更全，可在本地 `python web/app.py` 使用。
