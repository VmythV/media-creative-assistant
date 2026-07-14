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

- [x] 按设计文档 2.1 验收标准逐条验证 — 2026-07-13 以真实素材（10 张照片转片段）+ 真实模型完成：①导入分析分类 ②推荐附理由 ③中文转写（faster-whisper small，合成语音视频验证）+ SRT ④方案有叙事结构且 IR 校验通过 ⑤Resolve Studio 一键生成项目/时间线/8 片段/SRT 入媒体池 ⑥降级四件套（IR/清单/FCPXML/SRT） ⑦二次分析命中缓存 ⑧工具调用见 /api/logs
- [x] 修复验收中发现的问题 — 无阻塞问题；发现系统 ffmpeg 为精简编译（无 libass/drawtext），成片渲染需用 PNG overlay 方案（见第二阶段 render_video）
- [x] 更新 README 与使用说明

## M6 照片素材与成片渲染（设计文档 §9）

- [x] `image_to_clip` 工具（EXIF 烘焙 + Ken Burns；横裁切/竖模糊背景）
- [x] 导入 API 支持图片扩展名，转换后注册为素材进入分析管线
- [x] `render_video` 渲染模块（IR trim/concat + Pillow 字幕 PNG overlay）
- [x] `POST /api/plans/{plan_id}/render` 接口（异步 + SSE + 产物写回）
- [x] 前端：方案页渲染成片入口；素材导入说明含图片
- [x] M6 验证：pytest 5 项通过（27 passed 全量回归）；真实照片 API 导入自动转片段；真实方案渲染出 32s 成片（字幕烧录）且 render_video 入执行日志

## M7 自然语言修订方案（设计文档 §10）

- [x] `revise_plan`：当前方案 + 修订指令 → 新方案（复用受限格式与校验重试循环）
- [x] `diff_plans`：确定性方案差异（新增/删除/修改/顺序）
- [x] `POST /api/plans/{plan_id}/revise`（新方案行，revised_from/diff 落库）
- [x] 执行接口允许 executed 状态重执行（回滚支持）
- [x] 前端：方案卡修订输入框 + 差异展示 + 修订来源标记
- [x] M7 验证：pytest 4 项通过（31 passed 全量）；真实指令"压缩到20秒、铺垫只留风铃、结尾只留夕阳"修订准确（diff：删 3 段、32s→20s），修订版渲染出 20s 成片

## M8 背景音乐与成片预览（设计文档 §11）

- [x] IR v0.2：音频轨 + MusicClip（gain/fade/loop；单条限制），校验器兼容 0.1
- [x] `PUT/DELETE /api/plans/{plan_id}/music`（确定性写 IR，校验音频流）
- [x] 渲染器配乐混音 pass（loop 截齐 + 音量 + 首尾 fade + amix，视频流 copy）
- [x] Resolve：配乐随 sources 入媒体池（时间线定位为脚本 API 限制，已记录）
- [x] `/output` 静态托管 + 渲染结果 video_url + 前端 `<video>` 内嵌预览
- [x] 前端：方案卡配乐设置/移除入口
- [x] M8 验证：pytest 4 项通过（35 passed 全量）；真实方案挂合成氛围配乐渲染，成片含可闻 BGM（mean −29.8dB），浏览器预览 URL 200

## M9 转场效果（设计文档 §12）

- [x] IR v0.3：Clip.transition（类型白名单 + 时长/位置约束校验）
- [x] 确定性转换：转场钳制（白名单过滤 + 时长 clamp）+ 字幕独占时间槽 + timeline_duration 扣减
- [x] 渲染器：xfade/acrossfade 链式渲染（混合硬切 concat；无转场保持流复制快路径；settb 统一 timebase）
- [x] Planning 提示词：transition 字段 + 节奏选型指引；diff_plans 转场变化检测
- [x] Resolve adapter：转场不支持提示（summary 记录）；前端片段列表转场标记
- [x] M9 验证：pytest 5 项通过（40 passed 全量）；真实方案（qwen3.7-max）自主为 6 片段选 5 处转场（fade/dissolve），成片 21.1s = Σ片段 24s − 转场重叠 3s，抽帧确认 fade/dissolve 混合中间态与字幕沿用规则

## M10 Resolve 时间线带转场与配乐（设计文档 §13）

- [x] export_fcpxml：转场元素（居中对齐数学：媒体入点/spine 时长偏移 t/2）；无转场输出保持不变
- [x] Resolve adapter：含转场走 ImportTimelineFromFile(FCPXML) 路径；无转场保持 AppendToTimeline
- [x] Resolve adapter：配乐 AppendToTimeline(mediaType=2, recordFrame) 入新增音频轨（截齐时间线，失败降级媒体池）
- [x] 前端：执行结果卡转场/配乐状态展示
- [x] M10 验证：pytest 3 项通过（43 passed 全量）；真实方案（6 片段 5 转场 + 配乐）执行后 Resolve 时间线 525 帧 = IR 21.0s，V1 轨 5 处交叉叠化位置/时长与 IR 一致，配乐入 A2 轨铺满（脚本核实）
- [x] 转场类型精确映射（M10 补强）：逐名探测 24 候选得出 FCPX 效果名词汇表（4 种可达类型，参数不被识别），10 种 IR 类型按语义映射；端到端验证 fade/fadewhite/wipeleft/circleclose → 交叉叠化/浸入颜色叠化/边缘划像/椭圆展开 全部正确（44 passed 全量）

## M11 User Memory（设计文档 §14）

- [x] MemoryProvider 接口 + SQLite 实现（memory_items 表，kind 枚举，归一化去重）
- [x] 偏好提取：修订成功后 LLM 受限格式提取持久偏好（一次性指令返回空），确定性写入
- [x] 注入：generate_plan/revise_plan system prompt 附加用户偏好
- [x] API：GET/POST/DELETE /api/memory；前端「偏好记忆」页签
- [x] M11 验证：pytest 5 项通过（49 passed 全量）；真实修订指令沉淀 2 条偏好（转场克制/字幕含蓄，一次性指令被正确忽略），下一次生成明显遵循（转场全叠化、字幕无感叹号留白风格）

---

第二阶段（M12 起）路线与优先级见 `docs/phase2-roadmap.md`；立项时逐里程碑迁入本清单。

## M12 统一自然语言入口（phase2-roadmap §1）

- [x] Intent Router：受限格式解析（reply + actions 白名单 + Pydantic 参数校验），状态简报注入
- [x] 动作执行器：串行执行（依赖动作等待完成），失败中断后续标 skipped，SSE 推进度
- [x] 会话：AgentSession 复用（对话历史 + 当前方案上下文，"再快一点"可指代）
- [x] unsupported 意图：能力边界清单驱动的"原因 + 手动指引"回复
- [x] API：POST /api/chat + GET /api/chat/{session_id}；前端「对话」页签（默认页，消息流 + 动作卡）
- [x] M12 验证：pytest 4 项通过（53 passed 全量）；真实走查："做15秒江南短片带字幕转场配乐渲染出来"一句话串联 create_plan→set_music→render 全部完成（成片 17s URL 200）；"节奏再快一点+加画中画"→ 修订正确指代当前方案（20s→13.5s）且画中画获得 Resolve 手动指引

## M13 输出规格与竖屏（phase2-roadmap §2）

- [x] IR v0.4：render 交付规格（width/height/fill 模式，偶数校验），与时间线规格解耦
- [x] 渲染器：按 render 规格输出；fill 三模式（pad 兼容现状 / crop 裁满 / blur 模糊背景居中）；字幕自适应
- [x] API：PUT/DELETE /plans/{id}/output（画幅预设 9:16/16:9/1:1，确定性写 IR）
- [x] Resolve/FCPXML：时间线分辨率采用交付规格
- [x] 对话：set_output_spec 意图（白名单+提示词），能力边界清单移除"竖屏切换"
- [x] 前端：方案卡画幅选择（跟随素材/16:9/9:16/1:1）；成片卡显示分辨率
- [x] M13 验证：pytest 5 项通过（58 passed 全量）；真实对话"改成竖屏发抖音用重新渲染"→ set_output_spec(9:16)+render 自动串联，成片 1080×1920，抽帧确认模糊背景居中构图

## M14 BGM 推荐与音乐库（phase2-roadmap §3）

- [x] MusicTrack 表 + 曲库扫描（data/music 默认目录，ffprobe 时长/响度，失效清理）
- [x] 推荐：LLM 受限格式从曲库选曲（id 白名单校验，注入用户偏好，失败确定性兜底）
- [x] API：GET /api/music、POST /api/music/scan、POST /plans/{id}/music/recommend
- [x] 对话：set_music 的 mood 参数走推荐（结果带理由）
- [x] 前端：配乐框升级（曲库 AutoComplete + AI 推荐按钮 + 手动路径兜底 + 理由展示）
- [x] M14 验证：pytest 3 项通过（61 passed 全量）；真实对话"换首更安静的音乐"→ 从 3 首曲库精准选中宁静古筝（理由引用了用户偏好记忆）并自动重渲

## M15 字幕样式（phase2-roadmap §4）

- [x] IR v0.5：SubtitleTrack.style（预设/位置/字号比/颜色/描边/底条/字体族，颜色校验）
- [x] 渲染器：按 style 绘制（top/center/bottom 定位、半透明底条、hex 颜色、宋体/黑体切换）+ 窄画幅长字幕自动缩字
- [x] 预设表确定性维护（default/elegant/bold/minimal → 具体字段展开）
- [x] API：PUT/DELETE /plans/{id}/subtitle-style；对话 set_subtitle_style 意图（边界清单移除字幕样式）
- [x] Resolve：样式仅体现渲染成片（执行摘要标注）
- [x] 前端：方案卡样式预设/位置 Segmented 选择
- [x] M15 验证：pytest 3 项通过（64 passed 全量）；真实对话"字幕放到顶部换醒目黄色重新渲染"→ bold+top 应用并重渲，抽帧确认顶部黄字底条且自动缩字不溢出

## M16 Resolve 渲染与 AI 工具（phase2-roadmap §5）

- [x] adapter：render_with_resolve（建时间线 → SetRenderSettings/AddRenderJob/StartRendering → 状态轮询 SSE；JobStatus 本地化坑改用百分比判断）
- [x] run_render 支持 engine=ffmpeg|resolve；渲染 API body 加 engine；对话 render 意图带 engine
- [x] adapter：generate_speech（GenerateSpeech TTS；实测缺 Extras 返回错误字符串，透明报错含安装指引）；对话 generate_voiceover 意图（产物注册为素材）
- [x] 前端：渲染按钮加 Resolve 引擎入口
- [x] M16 验证：pytest 2 项通过（66 passed 全量）；真实对话"用 Resolve 渲染高质量版"→ 竖屏 1080×1920 h264 成片 3.3 秒渲完（含时间线转场配乐）；TTS 探测返回透明安装指引
- [ ] 暂缓：auto_captions 自动字幕（本机素材无真人对白，无验证条件）；DetectSceneCuts 对照（作用于时间线而非素材文件，与分析管线无消费点）
