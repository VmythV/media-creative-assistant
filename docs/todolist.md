# MVP 开发 TODOLIST

依据：`docs/mvp-technical-design.md` v1.0
规则：每完成一项勾选一项；里程碑末尾的"验证"项通过后才进入下一里程碑；与设计文档冲突时先改设计文档。

## M0 项目骨架

- [x] 环境检查（Python 3.12 / uv / FFmpeg / Node）— uv 0.9.28 / Python 3.12.8 / FFmpeg 8.1.2 / Node 24
- [x] backend 项目初始化（uv + pyproject + 目录结构）
- [x] 配置模块（pydantic-settings + `.env.example`）
- [x] SQLite 存储层（SQLAlchemy 模型：素材、分析缓存、会话、任务日志）
- [x] Tool Registry 基础框架（MCP 兼容的工具定义与注册）
- [x] `probe_media` 工具（ffprobe 元数据提取）
- [x] Capability Discovery（FFmpeg / Resolve / DashScope Key / Whisper / Ollama 检测）
- [x] FastAPI 入口 + `/api/capabilities` 接口
- [x] Resolve Studio 脚本 API 冒烟测试脚本 — 已通过：Resolve Studio 21.0.2.4 + Python 3.12.8 连接/建项目/建时间线成功
- [x] M0 验证：启动输出 Capability Registry；pytest 6 项全部通过

## M1 素材分析管线

- [x] `extract_audio` 工具（FFmpeg 提取 wav）
- [x] `detect_shots` 工具（PySceneDetect 镜头边界）
- [x] `sample_frames` 工具（按时间点抽帧）
- [x] `detect_audio_events` 工具（FFmpeg silencedetect/volumedetect 启发式）
- [x] VisionProvider 接口 + Qwen-VL（DashScope）实现
- [x] `analyze_frames` 工具（视觉分类/描述/质量/主体）
- [x] `transcribe_audio` 工具（faster-whisper 中文转写）— tiny 模型真实转写验证通过
- [x] 素材导入 API + 内容 hash 分析缓存
- [x] 分析管线编排（异步任务 + 进度记录 + SSE 推送）
- [x] Understanding Agent：聚合分析结果，产出素材分类 + 精彩片段候选评分（启发式 + 模型）
- [x] M1 验证：pytest 端到端管线通过，二次运行命中缓存（vision 不再调用模型）

## M2 剪辑方案与 Editing IR

- [x] Editing IR v0.1 schema（Pydantic）+ 校验器 + JSON Schema 导出
- [x] `validate_ir` 工具
- [x] LLMProvider 接口 + Qwen（DashScope）实现
- [x] Agent Runtime（Session 管理 + tool-use 循环）
- [x] Planning Agent：创作目标 + 素材分析 → 结构化剪辑方案（受限中间格式）
- [x] 方案 → Editing IR 确定性转换 + 校验失败自动重试一次
- [x] 精彩片段推荐 API（附推荐理由）
- [x] 剪辑方案 API（生成 / 查看 / 确认）
- [x] M2 验证：pytest 通过（mock LLM 产出方案 → IR 校验通过；非法方案自动重试成功）

## M3 Adapter 层

- [x] Resolve Adapter：连接 + 创建项目 + 导入素材
- [x] Resolve Adapter：按 IR 创建时间线 + 添加片段（trim）
- [x] Resolve Adapter：字幕（SRT 导入媒体池，一步右键上轨；API 限制已记录设计文档）
- [x] `export_fcpxml`（备用导入路径）
- [x] `export_edit_list`（Markdown 剪辑清单降级路径）
- [x] 降级逻辑：Resolve 不可用时自动走 IR 文件 + 剪辑清单 + FCPXML + SRT
- [x] 执行日志（工具调用输入/输出/错误落库 + `/api/logs`）
- [x] M3 验证：真实 Resolve 生成可编辑时间线（3 片段 trim 正确）；pytest 降级路径通过

## M4 Web 界面

- [x] 前端脚手架（Vite + React + TS + Ant Design）
- [x] 素材导入页（选择本地文件/目录，触发分析）
- [x] 分析结果展示（分类、镜头、转写、精彩片段推荐及理由）
- [x] 创作目标输入 + 剪辑方案展示（叙事结构可视化）
- [x] 方案确认/调整 + 执行（生成 Resolve 时间线或降级输出）
- [x] 执行日志与任务进度展示（SSE）
- [x] FastAPI 托管前端构建产物
- [x] M4 验证：服务端全流程验证通过（页面托管 / 导入 / 多镜头分析 / SSE 事件 / 降级推荐）；浏览器内含方案生成的完整体验需配置 DASHSCOPE_API_KEY 后走查

## M5 验收

- [ ] 按设计文档 2.1 验收标准逐条验证（**待用户提供 DASHSCOPE_API_KEY 与真实素材后进行**）
- [ ] 修复验收中发现的问题
- [x] 更新 README 与使用说明
