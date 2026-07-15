"""DaVinci Resolve Adapter：Editing IR → Resolve 项目/时间线/字幕。

同步阻塞实现（Resolve API 非线程安全），调用方用 asyncio.to_thread 包装。
每步失败抛 ResolveAdapterError，由执行层决定降级。
"""

import logging
import sys
import time
from pathlib import Path

from app.config import settings
from app.ir.exporters import export_fcpxml, export_srt
from app.ir.schema import AudioTrack, EditingIR, VideoTrack, timeline_duration

logger = logging.getLogger("mca.resolve")


class ResolveAdapterError(RuntimeError):
    pass


def _import_module():
    modules_path = str(Path(settings.resolve_script_api) / "Modules")
    if modules_path not in sys.path:
        sys.path.append(modules_path)
    try:
        import DaVinciResolveScript as dvr
    except ImportError as e:
        raise ResolveAdapterError(f"无法导入 DaVinciResolveScript: {e}") from e
    return dvr


def connect():
    """连接运行中的 Resolve；未运行时报错（不自动启动，避免打断用户）。"""
    dvr = _import_module()
    resolve = dvr.scriptapp("Resolve")
    if resolve is None:
        raise ResolveAdapterError(
            "无法连接 DaVinci Resolve：请确认 Resolve 正在运行，且外部脚本设置为 Local"
        )
    return resolve


def execute_ir(ir: EditingIR, *, progress=None) -> dict:
    """把校验通过的 IR 落成 Resolve 工程。返回执行摘要。

    progress: 可选回调 fn(step: str, detail: str)
    """

    def report(step: str, detail: str = "") -> None:
        logger.info("resolve: %s %s", step, detail)
        if progress:
            progress(step, detail)

    resolve = connect()
    report("connect", f"{resolve.GetProductName()} {resolve.GetVersionString()}")

    pm = resolve.GetProjectManager()
    project_name = f"{ir.project.name}-{time.strftime('%m%d-%H%M%S')}"
    project = pm.CreateProject(project_name)
    if project is None:
        raise ResolveAdapterError(f"创建项目失败: {project_name}")
    report("project", project_name)

    fps = ir.project.fps
    res = ir.render if ir.render is not None else ir.project.resolution  # 交付规格优先（v0.4）
    project.SetSetting("timelineFrameRate", str(int(fps) if fps == int(fps) else fps))
    project.SetSetting("timelineResolutionWidth", str(res.width))
    project.SetSetting("timelineResolutionHeight", str(res.height))

    media_pool = project.GetMediaPool()
    clips = [c for t in ir.tracks if isinstance(t, VideoTrack) for c in t.items]
    n_transitions = sum(1 for c in clips if c.transition)

    # 含转场走 FCPXML 导入路径（AppendToTimeline 无法插转场，设计文档 §13）
    if n_transitions:
        timeline = _build_timeline_fcpxml(media_pool, ir, report)
        transitions_result = {"count": n_transitions, "method": "fcpxml_import"}
    else:
        timeline = _build_timeline_append(media_pool, ir, fps, report)
        transitions_result = None
    project.SetCurrentTimeline(timeline)

    subtitle_result = _add_subtitles(media_pool, timeline, ir, report)
    music_result = _place_music(media_pool, timeline, ir, report)

    # 变速（M25）：脚本 API 无法可靠设置片段 retime，Resolve 时间线为原速，仅体现在渲染成片
    speed_clips = [(i, c.speed) for i, c in enumerate(clips, 1) if c.speed != 1.0]
    speed_result = None
    if speed_clips:
        detail = "、".join(f"第{i}段 {s}x" for i, s in speed_clips)
        speed_result = {"count": len(speed_clips), "method": "unsupported", "clips": detail}
        report("speed", f"{detail} 变速仅体现在渲染成片；Resolve 时间线为原速，请在检查器手动 Retime")

    pm.SaveProject()
    return {
        "project": project_name,
        "timeline": ir.project.name,
        "clips": len(clips),
        "subtitles": subtitle_result,
        "music": music_result,
        "transitions": transitions_result,
        "speed": speed_result,
    }


def _build_timeline_append(media_pool, ir: EditingIR, fps: float, report):
    """无转场路径：素材入池 + AppendToTimeline 逐片段 trim。"""
    paths = [s.path for s in ir.sources]
    items = media_pool.ImportMedia(paths)
    if not items:
        raise ResolveAdapterError("素材导入失败")
    # 按文件路径映射 MediaPoolItem（Resolve 返回顺序与输入一致性不保证）
    item_by_path: dict[str, object] = {}
    for item in items:
        fp = item.GetClipProperty("File Path")
        if fp:
            item_by_path[str(Path(fp).resolve())] = item
    report("import", f"导入素材 {len(items)} 个")

    timeline = media_pool.CreateEmptyTimeline(ir.project.name)
    if timeline is None:
        raise ResolveAdapterError("创建时间线失败")

    clip_infos = []
    for track in ir.tracks:
        if not isinstance(track, VideoTrack):
            continue
        for clip in track.items:
            source = next(s for s in ir.sources if s.id == clip.source_id)
            item = item_by_path.get(str(Path(source.path).resolve()))
            if item is None:
                raise ResolveAdapterError(f"素材未找到于媒体池: {source.path}")
            clip_infos.append(
                {
                    "mediaPoolItem": item,
                    "startFrame": round(clip.trim.start * fps),
                    "endFrame": max(round(clip.trim.end * fps) - 1, round(clip.trim.start * fps)),
                }
            )
    appended = media_pool.AppendToTimeline(clip_infos)
    if not appended:
        raise ResolveAdapterError("片段添加到时间线失败")
    report("timeline", f"时间线 {ir.project.name}：{len(clip_infos)} 个片段")
    return timeline


def _build_timeline_fcpxml(media_pool, ir: EditingIR, report):
    """转场路径：生成含 <transition> 的 FCPXML 1.9 → ImportTimelineFromFile。

    素材随导入按 media-rep 路径自动入媒体池；转场按 FCPX 效果名映射为 4 种
    Resolve 转场（叠化/浸入颜色叠化/边缘划像/椭圆展开，实测词汇表见
    docs/resolve-scripting-api.md §5），方向/颜色参数不被导入器识别。
    """
    fcpxml_path = settings.data_dir / "output" / f"{ir.project.name}.timeline.fcpxml"
    fcpxml_path.parent.mkdir(parents=True, exist_ok=True)
    fcpxml_path.write_text(export_fcpxml(ir), encoding="utf-8")

    timeline = media_pool.ImportTimelineFromFile(
        str(fcpxml_path), {"timelineName": ir.project.name}
    )
    if timeline is None:
        raise ResolveAdapterError(f"FCPXML 时间线导入失败: {fcpxml_path}")
    report("timeline", f"时间线 {timeline.GetName()}（FCPXML 导入，含转场）")
    return timeline


def _place_music(media_pool, timeline, ir: EditingIR, report) -> dict | None:
    """配乐直接放到新增音频轨（AppendToTimeline mediaType=2 + recordFrame，实测可行）。

    片段音量/淡入淡出为脚本 API 空白，响度处理仍在渲染侧；失败降级为仅入媒体池。
    """
    music = next((m for t in ir.tracks if isinstance(t, AudioTrack) for m in t.items), None)
    if music is None:
        return None
    src = next(s for s in ir.sources if s.id == music.source_id)
    filename = Path(src.path).name
    try:
        items = media_pool.ImportMedia([src.path])
        if not items:
            raise ResolveAdapterError(f"配乐导入媒体池失败: {src.path}")
        # 新增独立音频轨，避免与视频片段联动音轨重叠
        timeline.AddTrack("audio", "stereo")
        track_index = timeline.GetTrackCount("audio")
        fps = ir.project.fps
        end_frame = max(round(min(timeline_duration(ir), src.duration) * fps) - 1, 0)
        appended = media_pool.AppendToTimeline([{
            "mediaPoolItem": items[0], "startFrame": 0, "endFrame": end_frame,
            "mediaType": 2, "trackIndex": track_index,
            "recordFrame": timeline.GetStartFrame(),
        }])
        if not appended:
            raise ResolveAdapterError("配乐 AppendToTimeline 失败")
        report("music", f"配乐 {filename} 已放置到 A{track_index} 轨（按时间线截齐）")
        return {"file": filename, "method": "timeline", "track": track_index}
    except Exception as e:  # noqa: BLE001 - 配乐失败不阻断时间线交付
        logger.warning("配乐入轨失败，降级为媒体池: %s", e)
        report("music", f"配乐 {filename} 已入媒体池，拖到音频轨即可")
        return {"file": filename, "method": "media_pool"}


def _add_subtitles(media_pool, timeline, ir: EditingIR, report) -> dict:
    """字幕：生成 SRT 并导入 Resolve 媒体池。

    Resolve 21 脚本 API 无法直接把 SRT 写入字幕轨（实测仅有 CreateSubtitlesFromAudio），
    导入媒体池后用户在 Resolve 中右键 "Insert Selected Subtitles to Timeline" 一步完成。
    """
    srt = export_srt(ir)
    if srt is None:
        return {"count": 0, "method": "none"}
    styled = any(getattr(t, "style", None) for t in ir.tracks if t.type == "subtitle")
    if styled:
        report("subtitle", "字幕样式仅体现在渲染成片；Resolve 内请用字幕轨样式面板调整")
    srt_path = settings.data_dir / "output" / f"{ir.project.name}.srt"
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(srt, encoding="utf-8")
    count = srt.count("-->")

    try:
        items = media_pool.ImportMedia([str(srt_path)])
    except Exception as e:  # noqa: BLE001 - 字幕失败不阻断时间线交付
        logger.warning("SRT 导入媒体池失败: %s", e)
        items = None
    if items:
        # 尝试直接上轨（部分版本不支持，失败无副作用）
        try:
            appended = media_pool.AppendToTimeline(items)
            if appended and any(appended):
                report("subtitle", "字幕已导入并添加到时间线")
                return {"count": count, "method": "timeline", "srt_path": str(srt_path)}
        except Exception:  # noqa: BLE001
            pass
        report(
            "subtitle",
            "SRT 已导入媒体池：在 Resolve 中右键该字幕 → Insert Selected Subtitles to Timeline",
        )
        return {"count": count, "method": "media_pool", "srt_path": str(srt_path)}
    report("subtitle", f"SRT 已生成：{srt_path}（可在 Resolve 中手动导入）")
    return {"count": count, "method": "manual", "srt_path": str(srt_path)}


def render_with_resolve(ir: EditingIR, out_dir: Path, *, filename: str | None = None,
                        progress=None) -> dict:
    """Resolve 渲染管线（M16）：建时间线 → 渲染队列 → 轮询完成。返回 {video, duration, resolve}。

    时间线含转场与配乐（M10）；字幕不在时间线上，成片不含字幕（提示走 ffmpeg 引擎或自动字幕）。
    """

    def report(step: str, detail: str = "") -> None:
        logger.info("resolve-render: %s %s", step, detail)
        if progress:
            progress(step, detail)

    summary = execute_ir(ir, progress=progress)

    resolve = connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None:
        raise ResolveAdapterError("渲染失败：当前项目丢失")
    out_dir.mkdir(parents=True, exist_ok=True)
    name = filename or f"{ir.project.name}_resolve"
    res = ir.render if ir.render is not None else ir.project.resolution

    if not project.SetCurrentRenderFormatAndCodec("mp4", "H264"):
        logger.warning("设置渲染格式 mp4/H264 失败，沿用当前格式")
    project.SetCurrentRenderMode(1)  # Single clip
    ok = project.SetRenderSettings({
        "SelectAllFrames": True,
        "TargetDir": str(out_dir),
        "CustomName": name,
        "FormatWidth": res.width,
        "FormatHeight": res.height,
        "ExportVideo": True,
        "ExportAudio": True,
    })
    if not ok:
        raise ResolveAdapterError("SetRenderSettings 失败")
    job = project.AddRenderJob()
    if not job:
        raise ResolveAdapterError("AddRenderJob 失败")
    if not project.StartRendering(job):
        raise ResolveAdapterError("StartRendering 失败")
    report("render", f"渲染任务 {job} 已开始（{res.width}x{res.height}）")

    while project.IsRenderingInProgress():
        status = project.GetRenderJobStatus(job) or {}
        report("render", f"{status.get('CompletionPercentage', 0)}%")
        time.sleep(2)
    status = project.GetRenderJobStatus(job) or {}
    # JobStatus 会被本地化（中文版返回"完成"），用完成百分比判断
    if status.get("CompletionPercentage") != 100:
        raise ResolveAdapterError(f"渲染未完成: {status}")

    video = out_dir / f"{name}.mp4"
    if not video.is_file():  # Resolve 可能追加自定义后缀，兜底找最新 mp4
        candidates = sorted(out_dir.glob(f"{name}*.mp4"), key=lambda p: p.stat().st_mtime)
        if not candidates:
            raise ResolveAdapterError(f"渲染完成但未找到产物: {video}")
        video = candidates[-1]
    report("done", str(video))
    from app.ir.schema import timeline_duration

    return {"video": str(video), "duration": timeline_duration(ir), "resolve": summary}


def generate_speech(text: str, out_dir: Path, *, voice: str = "Female 1") -> dict:
    """AI 配音（M16）：Resolve GenerateSpeech。Extras 缺失/非 Studio 时透明报错。"""
    resolve = connect()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject() or pm.CreateProject(f"voiceover-{time.strftime('%H%M%S')}")
    item = project.GenerateSpeech(
        {"TextInput": text[:350], "VoiceModel": voice, "AddToTimeline": False}, "01:00:00:00"
    )
    if isinstance(item, str):  # 缺 Extras 包时返回错误字符串（实测）
        raise ResolveAdapterError(
            f"语音生成不可用（{item}）：请在 Resolve 菜单 Workspace → Extras Download Manager "
            "安装 AI Speech Generator 后重试"
        )
    if item is None:
        raise ResolveAdapterError(
            "语音生成不可用：请在 Resolve 菜单 → Extras Download Manager 安装 AI Speech Generator 后重试"
        )
    src = item.GetClipProperty("File Path")
    if not src or not Path(src).is_file():
        raise ResolveAdapterError("语音已生成但未找到音频文件")
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"voiceover_{int(time.time())}{Path(src).suffix}"
    import shutil

    shutil.copyfile(src, dest)
    return {"audio": str(dest), "text": text[:350]}
