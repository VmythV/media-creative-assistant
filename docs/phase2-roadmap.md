# 第二阶段路线图：自然语言驱动的视频创作

文档状态：v1.0（2026-07-13 确认）
上游：`docs/mvp-technical-design.md`（M0–M11 已完成）、`docs/resolve-scripting-api.md`（能力边界依据）
规则沿用：与本文档冲突先改文档；每里程碑设计→todolist→测试→真实走查→提交。

---

## 0. 北极星与设计原则

**北极星**：用户用自然语言描述想要的视频，AI 理解意图并尽可能自动完成；自动化做不到的部分，明确告知用户并给出具体的手动操作指引——绝不静默丢失用户的意图。

现状对照：「创作目标 → 方案 → IR → 渲染/Resolve」的主干已经存在且正是这个架构；**差距在覆盖面**——目前只有生成/修订两个动作走自然语言，配乐、渲染、执行、输出规格、字幕样式都是按钮和表单。第二阶段的主线就是把自然语言入口铺满全部能力。

**四条设计原则**（每个新里程碑都要过一遍）：

1. **自然语言优先**：每个新能力必须同时接入自然语言入口，按钮只是快捷方式。
2. **IR 是唯一通用契约**：AI 理解的终点永远是 IR 变更（或对 IR 的操作），渲染器、Resolve、降级产物都从 IR 出发。模型只产出受限格式，确定性代码转 IR——MVP 的风险控制原则不动摇。
3. **能力边界透明**：能做的直接做；做不了的（脚本 API 空白、渲染器限制）在响应和产物中标注"需在 XX 中手动：具体步骤"，来源是 Capability Registry + 已知空白清单（§8）。
4. **确定性兜底**：意图解析失败或参数越界时降级为澄清式追问，不猜测执行。

---

## 1. M12：统一自然语言入口（对话式指挥）—— P0，核心主线

**目标**：一个对话框指挥全系统。"给我做个30秒的竖屏旅行短片，配上舒缓的音乐，渲染出来" → 自动串联 生成方案 → 配乐 → 渲染。

**设计**：

- **Intent Router**（受限格式，延续风险控制）：LLM 把用户输入解析为意图序列：

  ```json
  {"reply": "好的，我来…（给用户的自然语言回应）",
   "actions": [
     {"intent": "create_plan", "params": {"goal": "30秒竖屏旅行短片"}},
     {"intent": "set_music", "params": {"mood": "舒缓"}},
     {"intent": "render", "params": {}}
   ]}
  ```

- **意图白名单**（v1）：`create_plan / revise_plan / set_music / remove_music / render / execute / set_output_spec / set_subtitle_style / list_assets / analyze_assets / query / unsupported`。每个 intent 映射到既有 API 能力函数，参数 Pydantic 校验，非法参数拒绝执行并追问。
- **上下文**：复用 `AgentSession` 表存对话历史与"当前方案 id"；"再快一点""换首歌"这类指代靠会话上下文解析。
- **`unsupported` 意图的处理是本原则核心**：回复必须包含 ①为什么做不了（引用能力边界）②在哪个工具里手动做、具体步骤 ③系统能帮到哪一步（如"我可以先把素材和时间线备好"）。空白清单硬编码自 §8。
- **API**：`POST /api/chat` body `{message, session_id?}`，SSE 推送 action 执行进度；前端新「对话」页签（首屏默认页），消息流 + action 卡片（每个动作的状态/结果内嵌现有组件）。
- **执行策略**：actions 串行执行，异步动作（生成/渲染）等待完成再执行下一个；重要不可逆动作（执行到 Resolve）v1 直接做（本地无副作用），后续可加确认开关。

**验证**：pytest（intent 解析受限格式/白名单外拒绝/参数校验/多动作串联 mock）；真实走查一句话串联三动作 + 一句"把字幕改成竖排"得到明确的"做不了+手动指引"回复。

---

## 2. M13：输出规格与竖屏（IR v0.4）—— P0

**目标**："改成竖屏的""出个 1080P 横版" 一句话切换输出规格。短视频平台（9:16）是当前最大的真实使用缺口。

**设计**：

- IR `render` 字段启用（schema 一直预留）：`{"resolution": {"width","height"}, "aspect": "16:9|9:16|1:1", "quality": "draft|final"}`；project.resolution 保持"素材主导的时间线规格"，render 是"交付规格"，二者解耦。
- **渲染器竖屏构图**：横素材→竖屏用「模糊背景 + 居中主体」（image_to_clip 已有同款算法，抽公共函数）；可选 crop 模式（中心裁切）。Ken Burns 参数按目标画幅重算。
- **Resolve**：时间线分辨率按 render 规格设置；横转竖提示可用 SmartReframe（executed summary 记录）。
- 字幕 PNG 尺寸/字号比例按输出规格自适应（现按 project.resolution，改为 render 规格）。
- 自然语言：`set_output_spec` intent；确定性写 IR（同配乐模式，不经模型改 IR）。

**验证**：pytest（v0.4 校验、竖屏渲染分辨率/模糊边、字幕自适应）；真实方案渲染 9:16 成片抽帧走查。

---

## 3. M14：BGM 推荐与音乐库 —— P1

**目标**："配个舒缓点的音乐" 不再需要手填绝对路径。

**设计**：

- 音乐库：`data/music/` 目录扫描登记（`POST /api/music/scan`，ffprobe 时长/响度，文件名/标签作为描述），MusicTrack 表。
- 推荐：模型从库清单中按方案情绪/节奏选择（受限格式 `{"music": "<登记id>", "reason": "..."}`，id 白名单校验），`set_music` intent 的 `mood` 参数走这条路；确定性写 IR 不变。
- 前端：方案卡配乐输入框升级为「从库选择 + 推荐理由展示 + 手动路径兜底」。
- 不做音乐生成与版权管理（留接口）。

**验证**：pytest（扫描登记/推荐白名单校验/mock 推荐）；真实"换首更安静的" 走查。

---

## 4. M15：字幕样式系统（IR v0.5）—— P1

**目标**："字幕放上面""换成黄色带底条" 可说可做。

**设计**：

- IR SubtitleTrack 增加 `style`：`{"preset": "default|elegant|bold|minimal", "position": "bottom|top|center", "size_ratio", "color", "outline", "background"}`（全部可选，缺省即现状）。
- 渲染器 Pillow 按 style 绘制（字体候选表已有，扩展位置/底条/颜色）；预设表确定性维护。
- Resolve/FCPXML 不支持字幕样式 → 执行摘要标注"样式仅体现在渲染成片，Resolve 内请用字幕轨样式面板调整"（原则 3）。
- `set_subtitle_style` intent；模型只能产出预设名+有限字段，确定性写 IR。

**验证**：pytest（style 校验/渲染位置与颜色断言——抽帧像素采样）；真实走查两种预设。

---

## 5. M16：Resolve 渲染与 AI 工具包 —— P1

**目标**：高质量交付路径 + 把 Resolve Studio 的 AI 能力变成系统工具。调研已完成（`resolve-scripting-api.md` §4.6/4.7），纯工程化。

**设计**：

- **Resolve 渲染**：`render_via_resolve` 工具：执行时间线 → `SetRenderSettings`（输出目录/格式/质量/字幕 BurnIn 可选）→ `AddRenderJob` → `StartRendering` → `GetRenderJobStatus` 轮询进度（SSE）。自然语言："用 Resolve 渲染高质量版"（`render` intent 加 `engine: ffmpeg|resolve` 参数）。
- **中文自动字幕**：执行到 Resolve 时可选 `CreateSubtitlesFromAudio`（AUTO_CAPTION_MANDARIN_SIMPLIFIED），字幕直接上字幕轨，绕过 SRT 手动步骤；与我们的 whisper 字幕二选一。
- **TTS 配音**：`GenerateSpeech` 包装为 `generate_voiceover` 工具（文本≤350字），替代 `say`；IR 旁白轨后续再议（先作为素材产出）。
- **场景检测对照**：`DetectSceneCuts` 作为 PySceneDetect 的备选（capability 可用时）。

**验证**：pytest（工具注册/降级）；真实 Resolve 渲染出片 + 自动字幕走查。

---

## 6. M17：素材管理与规模化 —— P2

- 素材删除/重新分析/缩略图（分析缓存里已有抽帧，直接复用做封面）。
- **简报检索化**（关键）：素材超过阈值（~20）时不再全量塞 prompt——先按创作目标用分类/评分/转写关键词筛 top-N 再入简报；为向量检索留接口。
- 前端素材卡片化（封面 + 分类标签 + 精彩片段数）。

---

## 7. M18：风格学习 —— P2（第二阶段旗舰）

**目标**："照这个视频的感觉剪"——从参考视频学节奏与结构。

**设计**：

- 参考视频分析器：复用现有管线（镜头检测/视觉/音频）提取**风格画像**（确定性统计 + 模型概括）：镜头时长分布（均值/方差）、剪辑密度（切点/秒）、转场使用率与类型、字幕密度与风格、音乐能量曲线。
- 画像存 Memory（kind=`business`，M11 枚举已留），命名可管理（"我的 vlog 风格"）。
- 生成注入：方案 prompt 附加画像（受限描述），target_duration/片段长度/转场选择向画像靠拢；diff 可显示"风格符合度"。
- 自然语言：`learn_style`（给路径）/ `apply_style`（按名引用）intent。

**验证**：快节奏参考片 vs 慢节奏参考片，生成方案的片段时长分布显著不同。

---

## 8. 能力边界清单（`unsupported` 意图的回复依据，随版本更新）

| 用户可能说的 | 结论 | 手动指引 |
| --- | --- | --- |
| 划像方向/浸入颜色（Resolve 内） | 脚本 API 不支持参数 | Resolve 时间线双击转场 → 检查器调方向/颜色（成片渲染是精确的） |
| 字幕直接上 Resolve 字幕轨（whisper 版） | API 空白 | 媒体池右键 SRT → Insert Selected Subtitles to Timeline；或用 M16 自动字幕 |
| 关键帧动画/复杂特效 | API 空白 | Resolve Fusion 页手动；系统可先备好时间线 |
| 片段音量/声像（Resolve 内） | API 空白 | Fairlight 页手动；渲染成片的响度系统可控 |
| 变速（慢动作/快放） | IR 未支持（可入 backlog） | Resolve 检查器 Retime；渲染侧待 IR 扩展 |
| 多视频轨/画中画 | IR 单主轨（可入 backlog） | Resolve 手动叠轨 |

---

## 9. 工程与架构（穿插进行，不占独立里程碑）

| 项 | 内容 | 触发时机 |
| --- | --- | --- |
| 本地模型兜底 | Ollama LLMProvider（D3 决策的"本地兜底"未兑现半边） | 断网/欠费投诉出现前 |
| 视觉分析提速 | 并发抽帧分析 + `fast_vision` 配置（qwen3-vl-plus 2s/帧 vs qwen3.7-plus 11-15s/帧） | M17 素材规模化时一并 |
| 任务持久化 | 后台任务落库可恢复（现 asyncio.create_task 重启即丢）。设计（M19，2026-07-14 实施）：`background_tasks` 表（kind/payload/status/detail）+ `spawn()` 统一包装所有后台任务；启动时 running→interrupted 并按 kind 恢复——analyze/analyze_batch（缓存命中近零成本）、render（确定性）、execute（新时间戳项目无副作用）、plan_generate/plan_revise（单次 LLM 调用，尊重用户原始意图）自动重跑；chat_actions 动作链上下文复杂，pending 动作标记中断由用户重发；`GET /api/tasks` 可观测 | 渲染任务变长后 |
| MCP Server 化 | 工具组包装独立 MCP Server（定义已兼容） | 有外部 Agent 复用需求时 |
| Workflow Integration 内嵌面板 | Web UI 嵌进 Resolve（Electron 插件 + RenderStart/Stop 回调） | 功能稳定后的产品形态升级 |

---

## 10. 优先级总表与推进顺序

| 优先级 | 里程碑 | 一句话 | 为什么在这个位置 |
| --- | --- | --- | --- |
| **P0** | M12 统一自然语言入口 | 一个对话框指挥全系统 | 北极星的直接兑现，后续所有能力都挂在它上面 |
| **P0** | M13 输出规格与竖屏 | "改成竖屏的" | 真实使用最大缺口；IR 契约扩展的第一步 |
| **P1** | M14 BGM 推荐 | "配个舒缓的音乐" | 当前体验最糟的一环 |
| **P1** | M15 字幕样式 | "字幕放上面换黄色" | 高频诉求，渲染侧全可控 |
| **P1** | M16 Resolve 渲染 + AI 工具 | "用 Resolve 出高质量版" | 调研完毕零风险，纯工程化 |
| **P2** | M17 素材规模化 | 素材多了也不乱 | 素材量上来之前的地基 |
| **P2** | M18 风格学习 | "照这个感觉剪" | 价值最高但最重，等对话入口与 IR 扩展稳定 |
| **P3** | §9 工程五项 | 兜底/提速/持久化/MCP/内嵌 | 按触发时机穿插 |

推进方式不变：每个里程碑先在 `mvp-technical-design.md` 或本文件补充细化设计 → todolist 立项 → 实现 + pytest → 真实走查 → 提交。
