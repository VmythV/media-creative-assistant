# AI Native Video Editing Agent — MVP 技术设计文档

文档状态：v1.0（已确认，作为开发标杆）
确认日期：2026-07-13
上游文档：`AI_Native_Video_Editing_Agent_Architecture.md`、`docs/ai-native-video-editing-agent-requirements.md`

本文档是 MVP 阶段开发的唯一标杆。需求文档中的"待确认问题"已在本文档第 1 节全部裁决；后续开发遇到与本文档冲突的想法，应先修订本文档再动代码。

---

## 1. 已确认的关键决策（Decision Log）

| # | 问题 | 决策 | 理由 |
| --- | --- | --- | --- |
| D1 | 目标用户 | **个人创作者** | 单机本地运行，无账号/协作体系，最快验证核心闭环 |
| D2 | MVP 输出形态 | **DaVinci Resolve 可编辑工程优先** | 符合"副驾驶"定位；Resolve 不可用时降级输出 Editing IR + 剪辑清单 |
| D3 | 模型策略 | **云端优先 + 本地兜底** | 视觉理解与方案生成走云端 API；语音识别用本地 Whisper；全部经 Model Provider 抽象，可切换 |
| D4 | 中文支持 | **第一版即支持中文语音识别与中文字幕** | 主要使用场景为中文素材，Whisper 中文成熟 |
| D5 | 后端语言 | **Python 3.12** | Resolve 脚本 API 即 Python；FFmpeg/Whisper/AI 生态最成熟 |
| D6 | 界面形态 | **本地 Web 应用**（FastAPI 后端 + React/TS 前端，浏览器访问） | 开发效率高，适合展示分析结果与方案；后续可套 Tauri 变桌面应用 |
| D7 | Agent 编排 | **自研轻量 Agent Runtime**（基于模型 API 的 tool-use 循环） | 完全可控、不被框架绑架，符合"不绑定单一提供商"约束 |
| D8 | 默认云端模型 | **通义 Qwen（DashScope）**：Qwen-VL 做视觉，Qwen 旗舰模型做规划 | 国内访问稳定、成本低、中文友好；通过 OpenAI 兼容接口调用，切换 Claude/GPT 只改配置 |

---

## 2. MVP 最终交付物定义

MVP 完成时，系统必须端到端支持以下用户旅程：

> 用户在浏览器中导入一组本地视频素材 → 系统自动分析（元数据、镜头、画面内容、语音转写）并展示分类与精彩片段推荐（附推荐理由）→ 用户输入创作目标（如"做一个 60 秒旅行短片，中文字幕"）→ 系统生成结构化剪辑方案（开场/铺垫/高潮/结尾）→ 用户确认或调整 → 系统生成 Editing IR 并在 DaVinci Resolve 中创建可继续编辑的时间线（含字幕轨）→ 用户在 Resolve 中继续精修。

### 2.1 验收标准（与需求文档 6.2 对齐）

1. 导入 ≥10 个本地视频素材（mp4/mov），全部完成分析并展示自动分类结果（风景/人物/产品/采访/空镜/废片等）。
2. 每个素材给出精彩片段推荐，且每条推荐附带可读的推荐理由。
3. 中文素材的对白能被转写，并可生成 SRT 格式中文字幕。
4. 系统生成的剪辑方案有明确叙事结构，并转换为通过 schema 校验的 Editing IR v0.1。
5. 本机安装 DaVinci Resolve 时：一键在 Resolve 中生成项目 + 时间线 + 片段 + 字幕轨，人可继续编辑。
6. Resolve 不可用时：降级输出 Editing IR JSON 文件 + 人类可读的剪辑清单（Markdown）。
7. 重复分析同一素材命中缓存，不重跑视觉/音频分析。
8. 所有工具调用（分析、IR 生成、Resolve 操作）在界面执行日志中可见，含输入摘要、输出摘要、错误信息。

### 2.2 明确不做（MVP 边界）

- ~~不做成片渲染输出（FFmpeg 渲染成片放第二阶段）~~（2026-07-13 M5 验收后提前纳入：真实使用中用户需要直接拿到 mp4 成片，见 §9 M6）。
- 不做自然语言修改已生成的 Timeline（IR diff/回滚放第二阶段）。
- 不做 BGM 推荐、特效模板、风格学习、多软件 Adapter、协作。
- 不做账号体系；Memory 只实现 Project Memory + Temporary Memory，User Memory 仅留接口。

---

## 3. 系统架构与模块划分

```
浏览器 (React/TS)
    │  HTTP / SSE
FastAPI 服务 (Python)
    │
Agent Runtime（自研 tool-use 循环 + Session 管理）
    ├── Planning Agent   （剪辑方案 → Editing IR）
    ├── Understanding Agent（素材理解结果聚合、意图解析）
    └── Memory System    （MemoryProvider 接口 + SQLite 实现）
    │
Tool Registry（MCP 兼容的工具定义，进程内执行）
    ├── media 工具组   → FFmpeg / ffprobe / PySceneDetect
    ├── vision 工具组  → Model Provider (Qwen-VL 默认)
    ├── audio 工具组   → faster-whisper (本地)
    ├── ir 工具组      → Editing IR 校验/转换
    └── editor 工具组  → Resolve Adapter / 降级导出
    │
Editing IR v0.1 (Pydantic schema + JSON Schema)
    │
Adapter 层
    ├── DaVinci Resolve Adapter（DaVinciResolveScript API，主路径）
    ├── FCPXML 导出（备用导入路径）
    └── Markdown 剪辑清单导出（降级路径）
```

### 3.1 模块职责

| 模块 | 职责 | 关键约束 |
| --- | --- | --- |
| Web UI | 素材导入、分析结果/片段推荐/方案展示、确认与调整、执行日志 | 只与 FastAPI 通信，不含业务逻辑 |
| API 服务 | REST 接口 + SSE 推送任务进度 | 分析任务全部异步（后台任务队列） |
| Agent Runtime | Session 上下文、模型调用、tool-use 循环、任务编排 | 不 import 任何具体软件 API，只调 Tool Registry |
| Capability Discovery | 启动时扫描 FFmpeg、Resolve、API Key、Whisper 模型、Ollama，生成 Capability Registry | 能力缺失时给出降级方案而非报错 |
| Video Intelligence | 元数据提取、抽帧、镜头检测、视觉标注、语音转写、结果缓存 | 结果全部落 SQLite，按文件内容 hash 缓存 |
| Editing IR | schema 定义、校验（时间线合法性、素材路径、时长、轨道引用）、序列化 | 带 version 字段；校验失败的 IR 绝不进入 Adapter |
| Adapter 层 | IR → Resolve 工程 / FCPXML / 剪辑清单 | 执行前二次校验；每步操作记录日志，失败可重试 |
| Memory | Project Memory（素材分析、剪辑历史）、Temporary Memory（会话内指令） | 经 `MemoryProvider` 接口（get/save/search），MVP 用 SQLite 实现 |

### 3.2 关于 MCP Tool Layer 的落地方式

MVP 阶段工具以**进程内 Tool Registry** 实现，但工具定义（名称、JSON Schema 参数、描述）完全遵循 MCP tool 规范。这样：

- Agent Runtime 与工具解耦的架构目标即刻达成；
- 第二阶段可将 FFmpeg/Resolve 工具组无改动地包装为独立 MCP Server（stdio），供外部 Agent 复用；
- 避免 MVP 阶段为进程间通信付出调试成本。

第一阶段工具清单（对齐需求文档 5.6，并按实际管线细化）：

| 工具 | 说明 | 底层实现 |
| --- | --- | --- |
| `probe_media(path)` | 提取时长/分辨率/帧率/编码等元数据 | ffprobe |
| `extract_audio(path)` | 提取音频轨为 wav | FFmpeg |
| `detect_shots(path)` | 镜头边界检测 | PySceneDetect |
| `sample_frames(path, timestamps)` | 按时间点抽帧 | FFmpeg |
| `analyze_frames(images, task)` | 视觉理解（分类/描述/质量/主体） | Qwen-VL（经 Vision Provider） |
| `transcribe_audio(wav, lang)` | 语音转写（含中文），输出带时间戳分段 | faster-whisper |
| `detect_audio_events(wav)` | 静音/音乐/噪声区间检测 | FFmpeg silencedetect/volumedetect（启发式） |
| `validate_ir(ir_json)` | Editing IR 校验 | Pydantic schema |
| `create_resolve_timeline(ir_json)` | 在 Resolve 中生成项目/时间线/字幕 | DaVinciResolveScript |
| `export_fcpxml(ir_json)` | 导出 FCPXML 供手动导入 | 自研序列化 |
| `export_edit_list(ir_json)` | 导出 Markdown 剪辑清单 | 自研序列化 |

---

## 4. 技术选型明细

| 领域 | 选型 | 说明 |
| --- | --- | --- |
| 语言/运行时 | Python 3.12，包管理用 `uv` | 全部源码 UTF-8 |
| Web 框架 | FastAPI + uvicorn，SSE 推进度 | 异步任务用 FastAPI BackgroundTasks；量大后可换任务队列，不引入 Celery |
| 前端 | React + TypeScript + Vite，UI 库用 Ant Design | 本地开发 `vite dev`，交付时构建后由 FastAPI 托管静态文件 |
| 媒体处理 | FFmpeg/ffprobe（subprocess 调用，不用 ffmpeg-python 封装）、PySceneDetect | Capability Discovery 检测 PATH 中的 ffmpeg |
| 视觉模型 | 默认 Qwen-VL（DashScope OpenAI 兼容接口）；抽象为 `VisionProvider`，预留 Claude/GPT/Ollama-LLaVA 实现 | 抽帧后发送关键帧，不上传原始视频 |
| 规划模型 | 默认 Qwen 旗舰（DashScope）；抽象为 `LLMProvider` | 用 OpenAI SDK 指向 DashScope base_url，切换提供商只改配置 |
| 语音识别 | faster-whisper（本地，`small`/`medium` 模型），中文直出 | 无 GPU 时自动降级更小模型 |
| Editing IR | Pydantic v2 模型 + 导出 JSON Schema | schema 文件入库版本管理 |
| Resolve 集成 | DaVinciResolveScript Python API（主路径，本机为 **Resolve Studio 版**，外部脚本 API 完整可用）；FCPXML 导入（备用） | 见 6.2 风险 |
| 存储 | SQLite（单文件，SQLAlchemy 2.0） | 存素材索引、分析缓存、Session、Memory、任务日志 |
| 配置 | `.env` + pydantic-settings | API Key 只从环境读取，绝不入库/入 git |

---

## 5. Editing IR v0.1 Schema

核心结构（Pydantic 定义为准，此处为示例）：

```json
{
  "version": "0.1",
  "project": {
    "name": "tokyo-trip-60s",
    "fps": 25,
    "resolution": { "width": 1920, "height": 1080 }
  },
  "sources": [
    { "id": "src_001", "path": "/abs/path/video001.mp4", "duration": 45.2 }
  ],
  "tracks": [
    {
      "type": "video",
      "index": 1,
      "items": [
        {
          "type": "clip",
          "source_id": "src_001",
          "trim": { "start": 3.0, "end": 12.0 },
          "role": "opening",
          "reason": "开场航拍，画面稳定、构图完整"
        }
      ]
    },
    {
      "type": "subtitle",
      "index": 1,
      "items": [
        { "type": "subtitle", "content": "东京之夜", "timeline_start": 0.0, "timeline_end": 3.5 }
      ]
    }
  ],
  "render": null
}
```

设计规则：

- `sources` 与 `tracks` 分离，clip 只引用 `source_id`，便于校验素材存在性。
- 每个 clip 带 `role`（opening/build/climax/ending/broll）与 `reason`（可解释性，需求 7.3）。
- 校验规则：source 路径存在、trim 在素材时长内、`start < end`、字幕时间不重叠、轨道 index 唯一。
- `render` 字段 MVP 恒为 null，schema 预留。
- v0.1 不含 transition/effect/audio track，字段在 schema 中预留枚举但校验器拒绝（防止模型幻觉产出未实现能力）。

---

## 6. 关键流程与风险

### 6.1 素材分析管线（异步）

```
导入 → hash 查缓存 → probe_media → extract_audio ─→ transcribe_audio → detect_audio_events
                        └→ detect_shots → 每镜头抽 1-3 帧 → analyze_frames（批量）
→ Understanding Agent 聚合 → 素材分类 + 精彩片段评分（启发式规则 + 模型判断）→ 落库 + SSE 推送
```

精彩片段评分 MVP 策略：先用启发式过滤（画面质量、时长、运动强度、是否有对白），再让模型对候选片段按创作目标打分并给理由——不追求全自动审美，理由展示给用户裁决。

### 6.2 主要风险与应对

| 风险 | 应对 |
| --- | --- |
| ~~DaVinci Resolve 外部脚本 API 在免费版受限~~（已解除：本机为 Studio 版，外部脚本 API 完整可用） | M0 已验证：Resolve Studio 21.0.2.4 + Python 3.12.8 连接/建项目/建时间线成功；FCPXML 导出保留为备用路径 |
| Resolve 21 脚本 API 无法把 SRT 直接写入字幕轨（实测仅有 CreateSubtitlesFromAudio） | Adapter 将 SRT 导入媒体池，用户在 Resolve 中右键 → Insert Selected Subtitles to Timeline 一步上轨；SRT 文件同时作为产物交付 |
| 项目位于 iCloud 同步的 ~/Documents，文件会被驱逐为占位符导致读取阻塞（已实际发生） | .venv/node_modules 用 `.nosync` 目录+符号链接；`PYTHONPYCACHEPREFIX` 指向本地缓存；建议将项目迁出 iCloud 同步范围或关闭"优化 Mac 存储" |
| Qwen-VL 对帧的理解质量不稳定 | 每镜头多帧投票；prompt 固化为版本化模板；保留人工修正分类的 UI 入口 |
| 大素材分析慢 | 全异步 + 进度推送 + 内容 hash 缓存；抽帧而非全帧分析 |
| 模型产出的 IR 不合法 | 模型只产出"剪辑方案"（受限中间格式），由确定性代码转换为 IR；IR 校验失败自动带错误信息重试一次，仍失败则暴露给用户 |

---

## 7. 项目结构

```
media-creative-assistant/
├── docs/                          # 需求与设计文档
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI 入口
│   │   ├── api/                   # 路由（assets, analysis, plans, execute, capabilities）
│   │   ├── runtime/               # Agent Runtime（session, tool-use loop, planning/understanding agent）
│   │   ├── tools/                 # Tool Registry + media/vision/audio/ir/editor 工具组
│   │   ├── providers/             # LLMProvider / VisionProvider（qwen, 预留 claude/openai/ollama）
│   │   ├── ir/                    # Editing IR schema + 校验 + 序列化（fcpxml, edit list）
│   │   ├── adapters/              # resolve_adapter.py
│   │   ├── memory/                # MemoryProvider 接口 + sqlite 实现
│   │   ├── capability/            # Capability Discovery
│   │   └── store/                 # SQLAlchemy 模型与迁移
│   └── tests/
├── frontend/                      # React + TS + Vite
└── .env.example
```

## 8. 开发里程碑

| 里程碑 | 内容 | 验证方式 |
| --- | --- | --- |
| M0 骨架 | 项目结构、FastAPI、SQLite、Capability Discovery、`probe_media`；**同步完成 Resolve Studio 脚本 API 冒烟测试（连接、建项目、建时间线）** | 启动即输出 Capability Registry；命令行完成一次元数据提取；Resolve 冒烟脚本跑通 |
| M1 分析管线 | 抽帧、镜头检测、Qwen-VL 视觉标注、Whisper 中文转写、缓存 | 10 个素材全部产出结构化分析结果，二次运行命中缓存 |
| M2 方案与 IR | Planning Agent、剪辑方案生成、Editing IR v0.1 + 校验、精彩片段推荐 | 给定创作目标，产出通过校验的 IR 与带理由的推荐 |
| M3 Adapter | Resolve Adapter（项目/时间线/片段/字幕）+ FCPXML/剪辑清单降级 | Resolve 中出现可编辑时间线；卸载 Resolve 场景走通降级 |
| M4 Web UI | 导入、分析展示、方案确认/调整、执行日志、SSE 进度 | 全流程在浏览器内完成 |
| M5 验收 | 按 2.1 逐条验收、修 bug、补文档 | 2.1 全部通过 |

每个里程碑合入 main 前跑通该里程碑的验证方式；M2 起 IR schema 的任何变更必须同步更新本文档第 5 节。

---

## 9. M6：照片素材与成片渲染（M5 验收后新增）

M5 真实验收（用户以 10 张照片剪旅行短片）暴露两个缺口，提前纳入开发：

### 9.1 照片素材导入（image_to_clip）

- 导入 API 扩展图片扩展名（jpg/jpeg/png/heic）；图片经 `image_to_clip` 工具转成视频片段后按现有流程注册为素材，复用全部分析管线（视觉分析对静态片段天然有效；无音频自动跳过音频步骤）。
- 转换规则：默认 4 秒 / 25fps / 1080p，Ken Burns 缓慢推近（zoompan）；EXIF 方向先烘焙进像素；横构图放大填满 16:9 后中心裁切，竖构图模糊放大背景 + 原图等高居中。
- 产物路径：`data/image_clips/<原文件名>_<内容hash前8位>.mp4`（防不同目录同名冲突）；已存在则直接复用（幂等）。

### 9.2 成片渲染（render_video）

- 新增渲染模块：按 IR 顺序对各片段 trim → 统一到 project 分辨率/fps → concat → 可选字幕烧录，输出 `data/output/plan_<id>/<项目名>.mp4`。
- 字幕烧录不依赖 libass/drawtext（实测 Homebrew ffmpeg 为精简编译均无）：用 Pillow 把每条字幕渲染成透明 PNG，按时间段 `overlay`。Pillow 为后端新增依赖。
- API：`POST /api/plans/{plan_id}/render`（要求方案已 executed 或 confirmed），异步执行，产物路径写回 plan.execution.artifacts 并经 SSE 推送。
- Resolve 可用与否均可渲染（渲染只依赖 IR 与源文件）；与 Resolve 时间线互为补充而非替代。

| 里程碑 | 内容 | 验证方式 |
| --- | --- | --- |
| M6 照片与渲染 | `image_to_clip` 工具 + 导入 API 图片支持；`render_video` 模块 + 渲染 API | pytest：图片导入→分析全流程、IR 渲染出 mp4 时长正确；真实照片走通照片→方案→成片 |

---

## 10. M7：自然语言修订方案（原第二阶段"IR diff/回滚"）

- 修订走与生成相同的风险控制路径：LLM 输入「当前方案 JSON + 修订指令 + 素材简报」，仍只产出受限方案格式，未提及部分保持不变；确定性转换 IR + 校验失败重试一次（复用 generate 的循环）。
- 修订产出**新的 EditPlan 行**（status: generating→draft），`plan.revised_from` 记录来源方案 id、`plan.revision_instruction` 记录指令；旧方案原样保留，即天然支持回滚（重新确认/执行旧版）。数据库无 schema 变更。
- `diff_plans(old, new)`：确定性差异计算（新增/删除/修改/顺序调整，按 asset_id + 时间区间重叠匹配），结果存 `plan.diff` 并在前端展示；不经过模型。
- API：`POST /api/plans/{plan_id}/revise`，body `{"instruction": "..."}`；要求源方案已有 IR。
- 执行接口放宽：`executed` 状态允许再次执行（Resolve 项目带时间戳后缀，重复执行无副作用），支持回滚旧版与修订版对比。

| 里程碑 | 内容 | 验证方式 |
| --- | --- | --- |
| M7 方案修订 | `revise_plan` + `diff_plans` + 修订 API + 前端修订/差异展示 | pytest：mock LLM 修订流全通、diff 正确；真实指令修订方案并渲染 |
