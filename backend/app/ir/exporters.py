"""Editing IR 序列化：SRT 字幕、FCPXML（备用导入）、Markdown 剪辑清单（降级路径）。"""

from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from app.ir.schema import EditingIR, SubtitleTrack, VideoTrack, timeline_duration

ROLE_LABELS = {"opening": "开场", "build": "铺垫", "climax": "高潮", "ending": "结尾", "broll": "空镜/穿插"}


def _srt_ts(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rem = divmod(ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def export_srt(ir: EditingIR) -> str | None:
    """字幕轨 → SRT 文本；无字幕返回 None。"""
    subtitle_tracks = [t for t in ir.tracks if isinstance(t, SubtitleTrack)]
    if not subtitle_tracks:
        return None
    items = sorted(
        (s for t in subtitle_tracks for s in t.items), key=lambda s: s.timeline_start
    )
    if not items:
        return None
    blocks = [
        f"{i}\n{_srt_ts(s.timeline_start)} --> {_srt_ts(s.timeline_end)}\n{s.content}\n"
        for i, s in enumerate(items, start=1)
    ]
    return "\n".join(blocks)


def export_edit_list(ir: EditingIR) -> str:
    """人类可读的 Markdown 剪辑清单（Resolve 不可用时的降级输出）。"""
    lines = [
        f"# 剪辑清单：{ir.project.name}",
        "",
        f"- 时间线时长：{timeline_duration(ir):.1f} 秒",
        f"- 帧率：{ir.project.fps} fps，分辨率：{ir.project.resolution.width}x{ir.project.resolution.height}",
        "",
        "## 素材",
        "",
    ]
    src_map = {s.id: s for s in ir.sources}
    for s in ir.sources:
        lines.append(f"- `{s.id}`：{s.path}（{s.duration:.1f}s）")
    lines += ["", "## 时间线", ""]

    pos = 0.0
    for track in ir.tracks:
        if not isinstance(track, VideoTrack):
            continue
        for i, clip in enumerate(track.items, start=1):
            src = src_map.get(clip.source_id)
            clip_len = clip.trim.end - clip.trim.start
            t_in = clip.transition.duration if clip.transition else 0.0
            trans = f"（{clip.transition.type} {t_in:.1f}s 转场进入）" if clip.transition else ""
            lines.append(
                f"{i}. **[{ROLE_LABELS.get(clip.role, clip.role)}]** "
                f"{Path(src.path).name if src else clip.source_id} "
                f"[{clip.trim.start:.1f}s → {clip.trim.end:.1f}s]（{clip_len:.1f}s，"
                f"时间线 {pos:.1f}s 起）{trans}"
            )
            if clip.reason:
                lines.append(f"   - 理由：{clip.reason}")
            pos += clip_len - t_in  # 转场消耗重叠，时间线位置按独占长度推进

    subtitles = [(t, s) for t in ir.tracks if isinstance(t, SubtitleTrack) for s in t.items]
    if subtitles:
        lines += ["", "## 字幕", ""]
        for _, s in sorted(subtitles, key=lambda x: x[1].timeline_start):
            lines.append(f"- [{s.timeline_start:.1f}s - {s.timeline_end:.1f}s] {s.content}")
    return "\n".join(lines) + "\n"


def _rational(seconds: float, fps: float) -> str:
    frames = round(seconds * fps)
    return f"{frames}/{int(fps)}s" if fps == int(fps) else f"{round(seconds * 1000)}/1000s"


def export_fcpxml(ir: EditingIR) -> str:
    """最小可用 FCPXML 1.9：视频轨片段（字幕经 SRT 单独交付）。"""
    fps = ir.project.fps
    res = ir.project.resolution
    frame_dur = f"1/{int(fps)}s" if fps == int(fps) else f"1000/{round(fps * 1000)}s"

    root = ET.Element("fcpxml", version="1.9")
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources, "format", id="r0", name=f"FFVideoFormat{res.height}p{int(fps)}",
        frameDuration=frame_dur, width=str(res.width), height=str(res.height),
    )
    for i, s in enumerate(ir.sources, start=1):
        asset = ET.SubElement(
            resources, "asset", id=f"r{i}", name=Path(s.path).stem,
            start="0s", duration=_rational(s.duration, fps), hasVideo="1",
        )
        ET.SubElement(asset, "media-rep", kind="original-media", src=f"file://{escape(str(s.path))}")

    event = ET.SubElement(ET.SubElement(root, "library"), "event", name=ir.project.name)
    project = ET.SubElement(event, "project", name=ir.project.name)
    sequence = ET.SubElement(project, "sequence", format="r0")
    spine = ET.SubElement(sequence, "spine")

    # 转场用居中对齐映射（设计文档 §13）：转入侧媒体入点 +t/2、spine 时长 −t/2，
    # 转出侧 spine 时长再 −t/2；两侧 handle 恰好消耗 IR trim 范围，总长 = Σ片段 − Σ转场。
    src_rid = {s.id: f"r{i}" for i, s in enumerate(ir.sources, start=1)}
    pos = 0.0
    for track in ir.tracks:
        if not isinstance(track, VideoTrack):
            continue
        for i, clip in enumerate(track.items):
            clip_len = clip.trim.end - clip.trim.start
            t_in = clip.transition.duration if (clip.transition and i > 0) else 0.0
            nxt = track.items[i + 1] if i + 1 < len(track.items) else None
            t_out = nxt.transition.duration if nxt and nxt.transition else 0.0
            if t_in:
                # Resolve 对 FCPXML 转场效果名的识别未文档化，统一映射 Cross Dissolve（实测可导入）
                ET.SubElement(
                    spine, "transition", name="Cross Dissolve",
                    offset=_rational(pos - t_in / 2, fps), duration=_rational(t_in, fps),
                )
            ET.SubElement(
                spine, "asset-clip",
                ref=src_rid[clip.source_id], name=clip.role,
                offset=_rational(pos, fps),
                start=_rational(clip.trim.start + t_in / 2, fps),
                duration=_rational(clip_len - t_in / 2 - t_out / 2, fps),
            )
            pos += clip_len - t_in / 2 - t_out / 2

    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n' + ET.tostring(
        root, encoding="unicode"
    )
