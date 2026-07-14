# DaVinci Resolve 脚本 API 能力抽象

来源：本机官方文档 `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/README.txt`（2026-05-26 更新）+ CHANGELOG，对应 **Resolve Studio 21.0.2.4**。
实测验证：`backend/scripts/resolve_capability_probe.py`（2026-07-13 在本机 Studio 21.0.2.4 上运行通过）。

> 用途：为 Adapter 层扩展提供能力地图。很多功能藏在**非直觉的 API 名字**下（见 §3 速查表），设计新功能前先查本文与官方 README。

## 1. 接入与运行模式

- **语言**：Python 3.6+ / Lua 5.1，经 `fusionscript.so` 连接；本项目 `resolve_adapter._import_module()` 已封装。
- **权限**：Preferences → System → General → External scripting using（None / Local / Network）。Network 模式可跨机器控制（注意安全）。
- **Headless**：`Resolve -nogui` 无界面启动，脚本 API 全部可用 → 流水线可在无人值守环境跑 Resolve。
- **菜单集成**：脚本放 `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/{Utility,Edit,Color,Deliver}` 即出现在 Workspace → Scripts 菜单。
- **API 非线程安全**：同步阻塞调用（本项目用 `asyncio.to_thread` 包装）。
- 对象均可自省：Python `dir(obj)` 可列出全部方法（官方 README 只列"常用"函数，自省可能发现更多）。

## 2. 对象模型

```
Resolve
├── ProjectManager ── Project ─┬─ MediaPool ── Folder ── MediaPoolItem
│   （项目库/数据库/云项目）    ├─ Timeline ── TimelineItem ── Graph（调色节点图）
│                              └─ Gallery ── GalleryStillAlbum ── GalleryStill
├── MediaStorage（挂载卷/文件系统视图）
└── Fusion()（完整 Fusion 脚本体系入口，另一套 API）
```

## 3. "功能 → API"速查表（重点：名字不直觉的能力）

| 想做的事 | 实际 API（注意归属对象） | 备注 |
| --- | --- | --- |
| 把音频精确放到时间线某轨某位置 | **`MediaPool.AppendToTimeline([{clipInfo}])`**，clipInfo 带 `mediaType=2, trackIndex, recordFrame` | 是 MediaPool 的方法不是 Timeline 的；✅ 本机实测可行（§5） |
| 给时间线加转场 | 无直接 API；**`MediaPool.ImportTimelineFromFile(fcpxml)`** 导入带 `<transition>` 的 FCPXML | ✅ 实测可行（§5）；EDL `D` 语法同样可行 |
| 视频片段精确 trim 入轨 | 同上 `AppendToTimeline`，clipInfo `startFrame/endFrame`（素材内）+ `recordFrame`（时间线位置） | 帧为单位；endFrame 语义近似闭区间 |
| 自动中文字幕 | **`Timeline.CreateSubtitlesFromAudio({autoCaptionSettings})`** | `AUTO_CAPTION_MANDARIN_SIMPLIFIED`；可设每行字数/Netflix 预设；需 Studio |
| AI 配音（TTS） | **`Project.GenerateSpeech({settings}, timecode)`** | 文本≤350 字，可自定义音色文件，可直接放时间线；需 Extras 包 |
| 场景切点检测 | **`Timeline.DetectSceneCuts()`** | 在时间线上自动切割 |
| 素材语音转写 | **`MediaPoolItem.TranscribeAudio(useSpeakerDetection)`** / `Folder.TranscribeAudio` | 21 版支持说话人检测；结果在 UI 侧，无读取转写文本的 API |
| Ken Burns / 裁切 / 变换 / 透明度 | **`TimelineItem.SetProperty(key, value)`** | `ZoomX/ZoomY/Pan/Tilt/RotationAngle/Crop*/Opacity/CompositeMode(30+ 混合模式)/Distortion` |
| 变速与光流补帧 | `TimelineItem.SetProperty("RetimeProcess", RETIME_OPTICAL_FLOW)` + `"MotionEstimation"` | 速度本身不可直接设（无 speed 属性） |
| 防抖 | **`TimelineItem.Stabilize()`** | |
| 竖屏智能重构图 | **`TimelineItem.SmartReframe()`** | 需 Studio |
| 智能遮罩 | `TimelineItem.CreateMagicMask(mode)` | mode: "F"/"B"/"BI" |
| 人声分离 | `Timeline/TimelineItem.SetVoiceIsolationState({isEnabled, amount})` | 轨道级和片段级都有 |
| 去运动模糊 | `MediaPoolItem.RemoveMotionBlur({deblurOption})` | 产出新 MediaPoolItem |
| 挂自定义数据到素材/标记 | **`MediaPoolItem.SetThirdPartyMetadata({dict})`**；Marker 的 `customData` 参数 | 可把我们的 asset_id/分析 JSON 写进 Resolve 项目，双向关联 |
| 渲染时烧录字幕 | `Project.SetRenderSettings({"ExportSubtitle": True, "SubtitleFormat": "BurnIn"})` | 也可 "SeparateFile"（SRT）/ "EmbeddedCaptions" |
| 一键导出并上传 YouTube/Vimeo | **`Project.RenderWithQuickExport(preset, {"EnableUpload": True})`** | 同步返回状态 |
| 导出当前帧截图 | `Project.ExportCurrentFrameAsStill(path)`；批量：`Timeline.GrabAllStills` + `GalleryStillAlbum.ExportStills` | |
| 波形自动对轨（双系统收声） | **`MediaPool.AutoSyncAudio([items], {settings})`** | AUDIO_SYNC_WAVEFORM/TIMECODE |
| 图片序列导入 | `MediaPool.ImportMedia([{"FilePath": "f_%03d.dpx", "StartIndex": 1, "EndIndex": 100}])` | printf 语法 |
| 音频样本级精确插入 | `Project.InsertAudioToCurrentTrackAtPlayhead(path, offsetSamples, durationSamples)` | Fairlight 页，依赖播放头+选中轨 |
| 复合片段 / Fusion 片段 | `Timeline.CreateCompoundClip([items])` / `CreateFusionClip` | |
| 片段加标题/发生器 | `Timeline.InsertTitleIntoTimeline(name)` / `InsertFusionTitleIntoTimeline` / `InsertGeneratorIntoTimeline` | 插入在播放头；名字用 UI 里的标题名 |
| 时间线导出交换格式 | `Timeline.Export(path, exportType)` | AAF/DRT/EDL/FCPXML 1.8-1.10/OTIO/CSV/ALE；1.10 产出 `.fcpxmld` 目录包，1.9 是单文件 |
| 代理/高清媒体切换 | `MediaPoolItem.LinkProxyMedia` / `LinkFullResolutionMedia` / `UnlinkProxyMedia` | |
| 监控增长中的文件（边录边剪） | `MediaPoolItem.MonitorGrowingFile()` | |
| 断链重连 | `MediaPool.RelinkClips([items], folderPath)` | |
| 项目归档/备份 | `ProjectManager.ArchiveProject` / `ExportProject` / `ImportProject` | .dra / .drp |

## 4. 分领域能力清单

### 4.1 项目与数据库（ProjectManager / Project）
建/删/加载/导入导出/归档项目；项目文件夹树；磁盘与 PostgreSQL 数据库切换；Blackmagic Cloud 云项目（创建/加载/导入/恢复）；项目设置全量读写 `GetSetting/SetSetting`（时间线分辨率、帧率——含 Drop Frame 语法 `"29.97 DF"`、SuperScale 0-4 档含 2x Enhanced 参数）。

### 4.2 媒体管理（MediaStorage / MediaPool / Folder / MediaPoolItem）
文件系统枚举（挂载卷/子目录/文件）；导入文件/目录/图片序列/子剪辑（`AddItemListToMediaPool` 支持 `{media, startFrame, endFrame}`）；媒体池文件夹树；素材元数据 + 第三方自定义元数据；标记（帧位置/颜色/时长/备注/customData 检索）；旗标与素材着色；克隆检测类目下的音视频自动同步；matte（片段蒙版/时间线蒙版）；代理链接；素材替换（`ReplaceClip` 保留元数据）；in/out 点（`SetMarkInOut`）；立体 3D 合成（`CreateStereoClip`）。

### 4.3 时间线构建（MediaPool / Timeline）
- 空时间线、从片段建时间线（`CreateTimelineFromClips` 支持 clipInfo trim）。
- **`AppendToTimeline([{clipInfo}])`：clipInfo = {mediaPoolItem, startFrame, endFrame, mediaType(1 视频/2 音频), trackIndex, recordFrame}** —— 这是唯一的"精确摆放"接口，视频音频通吃（✅ 实测）。
- 从文件建时间线：**AAF/EDL/XML/FCPXML/DRT/ADL/OTIO**（`ImportTimelineFromFile`，可控制是否连带导入素材、素材搜索路径）；AAF 可增量导入到现有时间线（`ImportIntoTimeline`）。
- 轨道管理：加/删/锁/启用/命名；音频轨支持全部声道格式（mono→7.1→adaptive36）。
- 片段删除（可 ripple）、链接/解链、复合片段、Fusion 片段、时间线复制（`DuplicateTimeline`）。
- 标题/发生器/OFX 发生器插入。
- 时间线设置读写（`Timeline.GetSetting/SetSetting`，可覆盖项目分辨率）。

### 4.4 片段级（TimelineItem）
变换（Zoom/Pan/Tilt/Rotation/Anchor/Flip）、裁切（含软边）、透明度、30+ 混合模式、镜头畸变、动态缩放缓动、缩放模式（Crop/Fit/Fill/Stretch）与滤波器（Lanczos 等 16 种）、变速处理方式、片段启用/禁用、take 选择器（多机位替代剪辑）、每片段 Fusion 合成管理（增删/导入导出 .comp）、标记/旗标/着色、LUT 导出。

### 4.5 调色（Graph / ColorGroup / Gallery）
节点图（节点数/标签/工具列表/启用/缓存）、每节点 LUT、CDL（Slope/Offset/Power/Saturation）、DRX 静帧调色应用（3 种对齐模式）、`CopyGrades` 跨片段拷贝、色彩组（组前/组后节点图）、调色版本管理（local/remote 版本增删改查）、静帧库（抓取/导入/导出/PowerGrade）、ARRI CDL+LUT、全部重置。

### 4.6 渲染交付（Project）
渲染设置字典（输出目录/自定义名/分辨率/帧率/像素宽高比/质量/H.264-H.265 编码 Profile/多遍编码/Alpha/网络优化/**字幕烧录-分离-内嵌三态**）；格式×编码器枚举查询；渲染队列（加/删/全删/状态含百分比/启停）；渲染预设管理与导入导出；Quick Export（含直接上传）；渲染模式（单片/逐片段）；数据烧印预设（`ImportBurnInPreset`，时间码水印等）。

### 4.7 Studio/AI 能力（多数需 Extras 包，API 返回 False 表示缺包或非 Studio）
转写（含说话人检测）、音频分类、自动字幕（16 语言含简繁中文）、TTS 语音生成、场景检测、智能遮罩、智能重构图、防抖、去运动模糊、人声分离、IntelliSearch 分析（含人脸识别）、Slate 场记板识别、杜比视界分析、立体转换。

### 4.8 Fairlight
播放头处样本级音频插入；Fairlight 预设应用（`ApplyFairlightPresetToCurrentTimeline`）。

## 5. 本机实测验证（2026-07-13，Studio 21.0.2.4）

脚本：`backend/scripts/resolve_capability_probe.py`。

1. **音频精确入轨 ✅**：`AppendToTimeline([{mediaPoolItem: wav, startFrame: 0, endFrame: 74, mediaType: 2, trackIndex: 1, recordFrame: 90100}])` 精确落位 A1 轨 90100 帧、时长 74 帧；同轨追加第二段到 90250 也成功 → **多段配乐/音效对位可行**，M8 设计文档记录的"脚本 API 无法定位音频到时间线"已过时（当时误判源于视频片段自带联动音轨占位）。
2. **FCPXML 转场导入 ✅**：含 `<transition name="Cross Dissolve" offset="50/25s" duration="25/25s"/>` 的 FCPXML 1.9 经 `ImportTimelineFromFile` 导入后，时间线 V1 轨出现真实转场对象（`GetItemListInTrack` 列出名为"交叉叠化"的 item，`GetMediaPoolItem()` 返回 None 可区分于普通片段）；`Export(EXPORT_FCPXML_1_9)` 回读仍保留 `<transition>` → **往返无损**。注意：两侧片段的 trim 必须留出转场余量（handle），FCPXML 内 spine offset 由我们显式计算。
3. **EDL 叠化导入 ✅**：EDL `D 025` 语法导入同样产生"交叉叠化"（素材按 reel 名匹配媒体池，命名需谨慎；FCPXML 按 `media-rep src` 路径匹配更可靠，推荐 FCPXML 路径）。
4. FCPXML **1.10 导出是 `.fcpxmld` 目录包**（内含 Info.fcpxml），1.9 是单文件——程序处理选 1.9。

## 6. 明确的 API 空白（截至 21.0.2.4）

> **文档完备性（2026-07-13 自省验证）**：官方 README 只列"能做什么"，从不列"做不了什么"。对运行中的 Resolve 各对象做 `dir()` 自省并与 README 求差集，结果**没有发现任何隐藏能力**——差集仅为 Deprecated 段落里的旧方法、每个对象通用的 `Print`、以及 `Resolve.SetHighPriority`。即 README 基本等于真实 API 全集（约 350 个方法），远小于 UI 人工操作面；下列空白是"接口不存在"级别的硬限制，只能靠间接路径（如 §5.2 的 FCPXML）或 Fusion 脚本体系绕行。

1. **无 `AddTransition` 类接口**——时间线上程序化插入转场只能走 §5.2 的 FCPXML/EDL 导入路径（对新建时间线适用；对已存在时间线无法补加）。
2. **字幕轨不可直接写内容**——SRT 只能入媒体池（UI 一步上轨），或 `CreateSubtitlesFromAudio` 从音频生成；无"把文本+时间码写上字幕轨"的接口。
3. **无关键帧写入接口**（调色/尺寸关键帧模式仅能切换模式）；动态缩放只能设缓动，不能设起止框。
4. **片段音量/声像不可设**（TimelineItem 无音频属性键；Fairlight 自动化不可脚本化）。
5. **转写结果读不出来**——`TranscribeAudio` 只返回 Bool，文本在 UI 侧。
6. 播放控制、UI 选中状态基本不可控（只有播放头时间码 `SetCurrentTimecode`）。
7. `AppendToTimeline` 的 clipInfo 无速度/变速参数。

## 7. 对本项目的应用机会（按价值排序）

1. **M10 候选：Resolve 时间线带转场**——执行路径改为：生成含 `<transition>` 的 FCPXML（IR 已有 transition 字段，导出器补 `<transition>` 元素 + spine offset 计算）→ `ImportTimelineFromFile` 替代现行 `AppendToTimeline` 逐片段方案。fade/dissolve 映射 Cross Dissolve 已验证；其余 xfade 类型（wipe/slide/circle）需逐个试 FCPXML `filter-video` 效果名，映射不上的降级 Cross Dissolve。
2. **配乐直接入轨**——`execute_ir` 中音频不再只入媒体池：`AppendToTimeline(mediaType=2, recordFrame=起始帧)` 直接放 A 轨（IR 的 MusicClip 已有全部所需信息）；gain/fade 属 API 空白（§6.4），响度仍靠渲染侧。
3. **Resolve 侧成片渲染**——`SetRenderSettings`（含字幕 BurnIn）+ `AddRenderJob` + `StartRendering` + `GetRenderJobStatus` 轮询，作为 ffmpeg 渲染的高质量替代（带调色/转场）。
4. **AI 工具替代/补充**：中文自动字幕（替代 SRT 手动上轨的一步）、`DetectSceneCuts`（对照 PySceneDetect）、`GenerateSpeech`（替代 `say`）。
5. **双向关联**：`SetThirdPartyMetadata` 把 asset_id/分析摘要写进素材；Marker customData 存 IR 片段 id，未来支持"在 Resolve 里改完读回"。
