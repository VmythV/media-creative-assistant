# AI Native Video Editing Agent 需求文档

文档状态：初版  
整理日期：2026-07-09  
来源文件：`AI_Native_Video_Editing_Agent_Architecture.md`

## 1. 背景与定位

本产品定位为面向视频创作者、剪辑师和内容团队的 AI Native Creative Assistant，而不是一个完全替代人的“AI 自动剪视频”工具。

核心理念是：人负责创意、审美和最终决策，AI 负责理解素材、生成剪辑方向、执行重复性工作，并通过专业剪辑软件完成可落地的工程输出。

产品类比：

- Cursor 之于程序员。
- Figma AI 之于设计师。
- 本产品之于视频创作者。

目标是形成“人类创意 + AI 理解能力 + 专业剪辑工具”的视频创作副驾驶系统。

## 2. 目标用户

- 独立视频创作者：需要快速整理素材、找到精彩片段、生成初剪方案。
- 专业剪辑师：希望减少重复操作，例如导入素材、分类、粗剪、字幕、基础时间线搭建。
- 新媒体与品牌内容团队：需要稳定产出短视频、宣传片、活动回顾等内容。
- 企业创作团队：需要多人协作、风格沉淀、项目记忆和统一工作流。

## 3. 产品目标

### 3.1 核心目标

- 理解视频、音频和项目上下文，生成可执行的剪辑计划。
- 将 AI 生成的剪辑计划转换为标准化 Editing IR，避免模型直接绑定具体剪辑软件。
- 通过 MCP Tool Layer 和软件适配器执行剪辑动作。
- 优先支持 DaVinci Resolve，逐步扩展到 Premiere Pro、After Effects、CapCut 等工具。
- 建立可切换、可搜索、可沉淀的 Memory System，学习用户和业务的创作偏好。

### 3.2 非目标

- 第一阶段不追求完全无人参与的成片生产。
- 第一阶段不同时深度支持所有剪辑软件。
- 第一阶段不把 Claude Code、Codex CLI 等 Coding Agent 作为主系统运行时，只作为 Specialist Agent 使用。
- 不让 LLM 直接调用具体剪辑软件 API，必须经过 Editing IR 和工具层。

## 4. 核心用户流程

1. 用户导入视频、音频、图片、字幕、品牌素材等项目资产。
2. 系统启动 Capability Discovery，检测本机或云端可用模型、剪辑软件、FFmpeg、存储和交互入口。
3. Video Intelligence Layer 对素材进行视觉、音频和元数据分析。
4. 用户通过 Web、Desktop 或 IM 交互提出创作目标，例如“做一个 60 秒旅行短片”。
5. Agent Runtime 结合用户目标、素材分析结果、项目记忆和业务风格生成 Editing Plan。
6. 系统将 Editing Plan 转换为 Editing IR。
7. MCP Runtime 根据环境能力选择合适工具，例如 DaVinci Adapter 或 FFmpeg MCP。
8. 适配器生成剪辑工程、时间线、字幕或渲染任务。
9. 用户预览结果并通过自然语言继续修改。
10. 系统输出最终视频或可继续编辑的专业软件工程。

## 5. 功能需求

### 5.1 交互层

交互层负责接收用户意图、展示分析结果、承载多轮修改。

需求：

- 支持 Web 或 Desktop 作为第一阶段主入口。
- 保留 IM 接入的扩展能力，例如企业群聊、工作流机器人。
- 支持自然语言输入剪辑目标、风格偏好、修改意见。
- 展示素材分析结果、精彩片段推荐、剪辑方案和执行状态。
- 支持用户确认、拒绝或修改 AI 生成的剪辑方案。

### 5.2 Conversation Layer

Conversation Layer 负责管理用户对话和任务上下文。

需求：

- 保存当前项目的多轮对话。
- 将用户自然语言请求转化为结构化任务。
- 支持引用前文，例如“把刚才那个片段再缩短一点”。
- 区分全局偏好、项目偏好和当前临时指令。

### 5.3 Agent Runtime

Agent Runtime 是系统核心调度层，统一管理 Planning、Understanding、Memory、Tools 和 Workspace。

需求：

- 包含 Planning Agent，用于拆解任务、生成剪辑计划。
- 包含 Understanding Agent，用于理解素材、用户意图和项目上下文。
- 集成 Memory System，用于读取和写入用户偏好、业务风格、项目历史。
- 管理 Session，避免无状态命令调用导致上下文丢失。
- 能够根据 Capability Registry 动态选择模型和工具执行路径。

### 5.4 Video Intelligence Layer

该层负责理解视频和音频素材，为剪辑计划提供依据。

视觉分析需求：

- 识别镜头边界、场景类型、画面质量、主体、运动强度。
- 标记可能适合开场、转场、高潮、结尾的片段。
- 支持素材自动分类，例如风景、人物、产品、采访、空镜、废片。

音频分析需求：

- 提取音频轨道。
- 支持语音识别和对白转写。
- 识别静音、噪声、音乐节奏和关键音频事件。
- 为字幕、节奏剪辑和片段推荐提供依据。

### 5.5 Editing IR

Editing IR 是系统最关键的中间表示层。AI 不直接控制 DaVinci、Premiere 或 After Effects，而是生成标准化、可校验、可转换的编辑描述。

需求：

- 表达 timeline、clip、trim、subtitle、transition、effect、audio、render settings 等核心剪辑信息。
- 支持版本号，便于未来扩展。
- 支持校验，确保时间线、素材路径、片段时长和轨道引用合法。
- 支持从自然语言计划转换为 IR。
- 支持由不同 Adapter 转换到不同剪辑软件。

示例结构：

```json
{
  "version": "0.1",
  "timeline": [
    {
      "type": "clip",
      "source": "video001.mp4",
      "trim": {
        "start": 3,
        "end": 12
      },
      "role": "opening"
    },
    {
      "type": "subtitle",
      "content": "Tokyo Night"
    }
  ]
}
```

### 5.6 MCP Tool Layer

MCP Tool Layer 用于隔离 Agent 和具体工具执行。Agent 只调用标准工具，不直接耦合底层软件 API。

第一阶段工具需求：

- `extract_audio()`：从视频中提取音频。
- `analyze_video()`：触发视觉分析。
- `create_timeline()`：创建时间线。
- `add_clip()`：向时间线添加片段。
- `add_subtitle()`：添加字幕。
- `render_video()`：渲染视频。

底层执行可以由 DaVinci Resolve API、FFmpeg、存储服务、模型服务或其他工具完成。

### 5.7 剪辑软件适配层

#### DaVinci Resolve Adapter

DaVinci Resolve 适合作为第一版重点支持对象。

需求：

- 创建项目。
- 导入素材。
- 创建 Timeline。
- 按 Editing IR 添加片段、调整 trim、组织轨道。
- 添加基础字幕。
- 调用渲染流程。
- 为 Fusion 特效控制预留扩展接口。

#### Premiere Pro Adapter

作为第二阶段或商业化阶段扩展。

需求：

- 支持 ExtendScript、CEP 或 UXP 插件方向。
- 将 Editing IR 转换为 Premiere 可执行动作。
- 支持商业团队常用工作流。

#### After Effects Adapter

更适合作为特效和 Motion Graphics 扩展。

需求：

- 支持 AI 生成特效、动画和动态图形模板。
- 与主剪辑时间线解耦，作为 Specialist Tool 使用。

### 5.8 Capability Discovery

系统启动时需要自动扫描当前环境能力，并形成 Capability Registry。

检测范围：

- 模型：GPT、Claude、Qwen、本地 LLM、Ollama。
- 视觉模型：云端 Vision、本地视觉模型、Qwen-VL、LLaVA。
- 剪辑软件：DaVinci Resolve、Premiere Pro、After Effects。
- 工具：FFmpeg、存储服务、渲染能力。
- 交互入口：Web、Desktop、IM。

需求：

- 生成结构化 Capability Registry。
- Agent 根据可用能力动态选择执行方案。
- 当能力缺失时给出可理解的降级方案，例如仅生成 Editing IR 或仅导出剪辑建议。

示例：

```json
{
  "capabilities": [
    {
      "type": "editor",
      "name": "davinci",
      "features": ["timeline", "subtitle", "fusion"]
    }
  ]
}
```

### 5.9 Model Provider 抽象

业务逻辑不能绑定单一模型提供商。

需求：

- 提供统一 LLM Interface，支持 GPT、Claude、Local LLM、Ollama。
- 提供统一 Vision Interface，支持 OpenAI Vision、Claude Vision、Qwen-VL、LLaVA。
- 支持按任务类型选择模型，例如视觉理解、计划生成、字幕润色、风格分析。
- 支持模型失败时降级或切换。

### 5.10 Memory System

Memory 必须接口化，并支持多种记忆类型，而不是单一用户记忆。

记忆类型：

- Global Memory：全局系统偏好和通用知识。
- User Memory：用户个人剪辑偏好。
- Business Profile：业务或品牌风格。
- Project Memory：当前项目上下文、素材信息、剪辑历史。
- Temporary Memory：当前会话的临时要求。

需求：

- 提供 `get()`、`save()`、`search()` 等基础接口。
- 支持 SQLite 作为本地 MVP 存储。
- 预留 PostgreSQL、Vector Database、Cloud Memory 扩展。
- 支持不同 Profile 切换，例如旅行视频、商业宣传、产品发布。

### 5.11 Coding Agent 集成

Claude Code、Codex CLI 等 Coding Agent 应作为 Specialist Agent，而不是主系统本身。

适用场景：

- 生成或修改 DaVinci Resolve 脚本。
- 开发 Fusion 插件。
- 生成 Adapter 代码。
- 修复项目中的自动化脚本。

需求：

- 主 Agent 负责任务判断和上下文管理。
- Coding Agent 只接收明确的开发任务。
- Session 或 Agent Runtime 需要保存工作目录、历史任务和上下文，避免简单无状态调用造成信息丢失。

## 6. MVP 范围

### 6.1 第一阶段 MVP

第一阶段重点验证“素材理解 -> 剪辑方案 -> Editing IR -> Resolve 工程生成”的闭环。

P0 功能：

- 导入本地视频素材。
- 使用 FFmpeg 提取基础元数据和音频。
- 对视频素材进行基础视觉分析。
- 对素材进行自动分类。
- 推荐精彩片段。
- 生成剪辑方案。
- 生成 Editing IR。
- 将 Editing IR 转换为 DaVinci Resolve 工程或时间线。
- 在界面中展示分析结果、剪辑方案和执行日志。

P1 功能：

- 自动字幕。
- BGM 推荐。
- AI 修改 Timeline。
- 基础特效模板。
- 多轮自然语言修改。

P2 功能：

- 学习个人剪辑风格。
- 多平台输出适配。
- Premiere Pro、After Effects、CapCut 扩展。
- 企业级项目协作和审批流程。

### 6.2 MVP 验收标准

- 用户能够导入一组素材，并获得自动分类结果。
- 系统能够推荐可用于成片的精彩片段。
- 系统能够生成结构化剪辑方案和合法 Editing IR。
- 系统能够在 DaVinci Resolve 中生成可继续编辑的时间线。
- 用户可以查看 AI 的剪辑理由，并决定是否采用。
- 当 DaVinci Resolve 不可用时，系统能够降级输出 Editing IR 或剪辑清单。

## 7. 非功能需求

### 7.1 可扩展性

- 编辑器、模型、存储、Memory Provider 必须接口化。
- Editing IR 需要支持版本演进。
- MCP Tool Layer 需要允许新增工具而不影响上层 Agent。

### 7.2 可靠性

- 所有工具调用需要记录输入、输出和错误。
- Editing IR 执行前必须校验。
- 剪辑软件适配器需要提供失败回滚或可重试机制。

### 7.3 可解释性

- AI 推荐片段时需要说明推荐原因。
- 剪辑方案需要展示结构，例如开场、铺垫、高潮、结尾。
- 用户需要能理解系统为什么选择某些镜头。

### 7.4 隐私与安全

- 本地素材默认不应无提示上传到云端。
- 云端模型分析需要明确用户授权。
- 企业版需要支持本地模型或私有化部署。

### 7.5 性能

- 大文件分析应异步执行。
- 视频分析结果需要缓存。
- 重复项目不应重复跑完整视觉和音频分析。

## 8. 可行性分析

### 8.1 总体判断

项目整体可行，但必须分阶段实施。第一阶段做成“AI 剪辑副驾驶 + DaVinci Resolve 工程生成器”可行性较高；一次性实现跨软件、自动成片、风格学习和企业协作的完整系统，复杂度过高。

推荐路线是先验证 Editing IR、DaVinci Adapter、素材分析和用户确认流程，再逐步扩展模型、Memory 和多软件适配。

### 8.2 技术可行性

可行点：

- FFmpeg 对元数据提取、转码、抽帧、音频提取支持成熟。
- DaVinci Resolve 提供 Python/Lua Scripting API，适合作为第一版专业剪辑软件适配目标。
- 视觉模型和语音识别模型已经具备基础素材理解能力，可用于分类、摘要和片段推荐。
- Editing IR 可以有效隔离 AI 计划和具体软件执行，降低长期维护成本。
- MCP Tool Layer 适合把模型推理、文件处理、剪辑软件控制拆成独立工具。

主要难点：

- “精彩片段”判断存在审美主观性，需要用户反馈和记忆系统逐步优化。
- 不同剪辑软件 API 能力差异较大，跨软件适配不能只靠简单字段映射。
- DaVinci Resolve 脚本执行、工程生成和渲染依赖本机安装环境，部署和调试成本较高。
- 视频理解的计算成本和处理时间较高，需要缓存、分段分析和异步任务。
- 自动修改 Timeline 容易出现不可预期结果，需要预览、确认和回滚机制。

### 8.3 产品可行性

高价值场景：

- 素材整理和分类。
- 粗剪方案生成。
- 精彩片段推荐。
- 自动创建专业剪辑工程。
- 基于用户反馈持续修改剪辑计划。

这些场景能显著减少前期整理和粗剪时间，同时保留创作者最终控制权，符合“AI 副驾驶”的产品定位。

需要谨慎的场景：

- 完全自动生成可发布成片。
- 自动学习复杂个人风格。
- 同时覆盖所有专业软件。
- 对企业流程做深度定制。

这些场景需要更多数据、交互反馈和工程适配，不适合作为 MVP 的主目标。

### 8.4 实施可行性分级

| 能力 | 可行性 | 建议阶段 | 说明 |
| --- | --- | --- | --- |
| FFmpeg 素材分析 | 高 | MVP | 技术成熟，适合作为基础能力 |
| 素材自动分类 | 高 | MVP | 视觉模型可支持，需允许人工修正 |
| 精彩片段推荐 | 中高 | MVP | 可先做启发式 + 模型判断 |
| Editing IR | 高 | MVP | 是架构核心，应尽早定义 |
| DaVinci 时间线生成 | 中高 | MVP | API 可用，但环境依赖明显 |
| 自然语言修改 Timeline | 中 | 第二阶段 | 需要 IR diff、预览和回滚 |
| 自动字幕 | 高 | 第二阶段 | 语音识别成熟，工程集成不难 |
| BGM 推荐 | 中 | 第二阶段 | 涉及版权、素材库和风格匹配 |
| 个人风格学习 | 中 | 第三阶段 | 需要长期数据和反馈闭环 |
| 多软件适配 | 中 | 第三阶段 | 需要 Adapter 长期维护 |
| 企业级工作流 | 中低 | 第三阶段 | 涉及权限、审计、协作、部署 |

### 8.5 风险与应对

| 风险 | 影响 | 应对策略 |
| --- | --- | --- |
| AI 生成的剪辑计划不可执行 | 时间线生成失败 | Editing IR schema 校验 + Adapter 执行前检查 |
| 用户不信任 AI 推荐 | 产品采用率低 | 展示推荐理由，并允许用户快速修改 |
| 多软件 API 差异大 | 维护成本高 | 先只深做 DaVinci，其他软件后置 |
| 云端分析带来隐私顾虑 | 企业用户难接受 | 支持本地模式、授权提示和私有化部署 |
| 视频处理耗时过长 | 体验差 | 异步任务、缓存、抽帧分析、进度反馈 |
| 个人风格学习效果不稳定 | 预期落差 | 初期使用可切换 Profile，不承诺完全自动学习 |

## 9. 推荐研发路线

### 阶段一：MVP 闭环

目标：完成从素材导入到 Resolve 时间线生成的最小可用闭环。

交付：

- 本地素材导入。
- FFmpeg 元数据和音频提取。
- 基础视觉分析。
- 自动分类和精彩片段推荐。
- Editing IR schema v0.1。
- DaVinci Resolve Adapter v0.1。
- Web 或 Desktop 原型界面。

### 阶段二：可交互修改

目标：让用户通过自然语言持续修改剪辑结果。

交付：

- IR diff 和修改记录。
- 自动字幕。
- BGM 推荐。
- 基础特效模板。
- Timeline 修改和回滚。
- 项目级 Memory。

### 阶段三：个性化和多平台

目标：形成面向专业创作者和团队的长期系统。

交付：

- User Memory 和 Business Profile。
- 多平台输出模板。
- Premiere Pro 和 After Effects Adapter。
- 企业级存储、权限、协作和审计。
- Specialist Coding Agent 集成。

## 10. 待确认问题

- 第一版主要面向个人创作者、专业剪辑师，还是企业内容团队？
- 第一版必须支持哪些输入格式和目标平台？
- 是否确定 DaVinci Resolve 为唯一 MVP 剪辑软件？
- 视频分析优先使用云端模型、本地模型，还是混合模式？
- 是否需要从第一版开始支持中文语音识别和中文字幕？
- MVP 输出是可编辑工程优先，还是成片视频优先？
- 用户素材是否允许上传云端，是否需要企业私有化部署路径？

