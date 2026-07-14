# Media Creative Assistant

面向视频创作者的 AI 剪辑副驾驶：素材理解 → 精彩片段推荐 → 剪辑方案 → DaVinci Resolve 可编辑工程。

设计标杆：`docs/mvp-technical-design.md` · 进度：`docs/todolist.md` · 第二阶段路线：`docs/phase2-roadmap.md`

## 环境要求

- macOS + Python 3.12（由 [uv](https://docs.astral.sh/uv/) 自动管理）
- FFmpeg（`brew install ffmpeg`）
- DaVinci Resolve Studio（运行中，External scripting 设为 Local）
- DashScope API Key（通义千问，用于视觉分析与方案生成）

## 快速开始

```bash
# 1. 配置 API Key
cp .env.example .env   # 编辑 .env 填入 DASHSCOPE_API_KEY

# 2. 启动后端（自动托管已构建的前端）
cd backend
uv sync
uv run uvicorn app.main:app --port 8000

# 3. 浏览器打开 http://127.0.0.1:8000
```

使用流程：素材页输入视频/照片目录路径导入（照片自动转成 4 秒推近片段）→ 分析全部 → 查看分类/精彩片段推荐 → 方案页输入创作目标（如"60秒旅行短片，中文字幕"）→ 确认方案 → 执行（在 Resolve 中生成时间线；SRT 字幕已导入媒体池，右键 → Insert Selected Subtitles to Timeline 上轨）→ 可对方案自然语言修订（带差异对比与回滚）、设置背景音乐 → "渲染成片"输出带字幕/配乐/转场（叠化、划像、压黑等，模型按节奏选择）的 mp4，并可在浏览器内直接预览播放（Resolve 时间线本身为硬切，转场需在 Resolve 内手动添加——脚本 API 限制）。

## 开发

```bash
cd backend && uv run pytest                # 后端测试（RUN_WHISPER_TESTS=1 启用转写测试）
cd frontend && npm install && npm run dev  # 前端热更新开发（代理到 :8000）
npm run build                              # 构建后由 FastAPI 托管
cd backend && uv run python scripts/resolve_smoke_test.py  # Resolve 连接冒烟测试
```

## 备注

- 项目已迁出 iCloud 同步范围（现位于 `~/program/`），早期的 `.nosync` 符号链接与 `PYTHONPYCACHEPREFIX` 变通方案均已移除，正常使用 `.venv` / `node_modules` 即可。**不要**把本项目放回 iCloud 同步目录（"优化 Mac 存储"会把文件驱逐成占位符，导致 Python 导入无限阻塞，曾实际发生）。
- 远程仓库：GitHub `origin`（https://github.com/VmythV/media-creative-assistant）；本地裸仓库备份在 `~/mca-backup.git`（`git push backup main`）。
