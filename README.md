

# recolor-tool — SVG / PDF 一键换色

适用场景：大数据图重新跑一次要很久，你只想快速试几套配色。直接对导出的
`PDF` / `SVG` 文件换色，秒出结果，不用重跑流程。

## 快速使用（二选一）

### 方式一：Windows 桌面版（独立窗口，即开即用）

[![下载 Windows 版](https://raw.githubusercontent.com/liuji70520/recolor-tool/main/docs/btn-download.svg)](https://github.com/liuji70520/recolor-tool/releases/latest/download/RecolorTool-Setup-v2.1.0.exe)

- **安装版**（推荐，像正常软件一样装到开始菜单）：
  [RecolorTool-Setup-v2.1.0.exe](https://github.com/liuji70520/recolor-tool/releases/latest/download/RecolorTool-Setup-v2.1.0.exe)
  —— 双击安装（按用户安装，不需要管理员），之后从开始菜单或桌面快捷方式启动。
- **便携版**（免安装，解压即用）：
  [RecolorTool-v2.1.0-portable.zip](https://github.com/liuji70520/recolor-tool/releases/latest/download/RecolorTool-v2.1.0-portable.zip)
  —— 解压后双击 `RecolorTool.exe`（或文件夹里的 `启动RecolorTool.cmd`）。

双击后弹出独立程序窗口（不再开浏览器标签页），**关掉窗口程序就退出，
端口随之释放，不会在后台残留**。历史会话保留在
`%LOCALAPPDATA%\RecolorTool\sessions`（7 天自动清理）。

> 注：仓库已公开，点击即可下载，无需登录。历史版本见
> [Releases](https://github.com/liuji70520/recolor-tool/releases) 页面。

### 方式二：网页版（在线使用，无需下载）
### ！！太久没维护可能会关闭，可以issue提醒
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

启动后默认弹出独立程序窗口（**关掉窗口程序即退出、端口随之释放**，不会在后台
残留）；没有 pywebview / WebView2 时自动回退为打开浏览器标签页。端口由系统
随机分配，不存在端口占用问题，可以同时开多个实例互不影响。

可用环境变量（都不是必需的）：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PORT` | `0`（随机） | 固定端口，局域网共享时用，如 `PORT=9000` |
| `HOST` | `127.0.0.1` | 改成 `0.0.0.0` 可让局域网访问 |
| `RECOLOR_UI` | `window` | `browser` = 强制用浏览器标签页 |
| `AUTO_OPEN` | `1` | 浏览器模式下 `0` = 不自动打开浏览器 |
| `IDLE_EXIT_SECONDS` | `120` | 页面断开多少秒后自动退出，`0` = 关闭 |
| `MAX_UPLOAD_MB` | `500` | 上传文件大小上限 |
| `HISTORY_DAYS` | `7` | 历史会话保留天数 |

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

网页版里点击颜色映射行（色块或颜色码），图中对应颜色的区域会变成醒目的
红色、其余区域变暗，方便确认每个映射颜色在图上落在哪里；再点一次或点
“取消高亮”恢复。

颜色映射表还标注了每个颜色的**类型**：填充 / 描边 / 填充+描边（位图为“像素”）。
文字、轴线、引线这类装饰元素基本都是描边，鼠标悬停徽标可查看 填充×N 描边×N
的明细，方便判断哪些是图的主要元素、哪些可以直接删掉不映射。
