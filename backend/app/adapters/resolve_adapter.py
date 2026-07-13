"""DaVinci Resolve Adapter：Editing IR → Resolve 项目/时间线/字幕。

同步阻塞实现（Resolve API 非线程安全），调用方用 asyncio.to_thread 包装。
每步失败抛 ResolveAdapterError，由执行层决定降级。
"""

import logging
import sys
import time
from pathlib import Path

from app.config import settings
from app.ir.exporters import export_srt
from app.ir.schema import AudioTrack, EditingIR, VideoTrack

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
    project.SetSetting("timelineFrameRate", str(int(fps) if fps == int(fps) else fps))
    project.SetSetting("timelineResolutionWidth", str(ir.project.resolution.width))
    project.SetSetting("timelineResolutionHeight", str(ir.project.resolution.height))

    media_pool = project.GetMediaPool()
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

    subtitle_result = _add_subtitles(media_pool, timeline, ir, report)

    # 配乐已随 sources 一并入媒体池（脚本 API 无法可靠定位音频到时间线，用户拖入 A1 即可）
    music = next((m for t in ir.tracks if isinstance(t, AudioTrack) for m in t.items), None)
    music_result = None
    if music is not None:
        src = next(s for s in ir.sources if s.id == music.source_id)
        music_result = {"file": Path(src.path).name, "method": "media_pool"}
        report("music", f"配乐 {music_result['file']} 已入媒体池，拖到 A1 轨即可")

    pm.SaveProject()
    return {
        "project": project_name,
        "timeline": ir.project.name,
        "clips": len(clip_infos),
        "subtitles": subtitle_result,
        "music": music_result,
    }


def _add_subtitles(media_pool, timeline, ir: EditingIR, report) -> dict:
    """字幕：生成 SRT 并导入 Resolve 媒体池。

    Resolve 21 脚本 API 无法直接把 SRT 写入字幕轨（实测仅有 CreateSubtitlesFromAudio），
    导入媒体池后用户在 Resolve 中右键 "Insert Selected Subtitles to Timeline" 一步完成。
    """
    srt = export_srt(ir)
    if srt is None:
        return {"count": 0, "method": "none"}
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
