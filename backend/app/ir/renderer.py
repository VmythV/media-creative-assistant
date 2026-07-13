"""IR → mp4 成片渲染（设计文档 §9.2）。

约束：本机 ffmpeg 可能是精简编译（无 libass/drawtext），字幕烧录用
Pillow 渲染透明 PNG 后按时间段 overlay 实现，只依赖 overlay 滤镜。
"""

import subprocess
from pathlib import Path
from typing import Callable

from app.ir.schema import EditingIR, SubtitleTrack, VideoTrack, timeline_duration

Progress = Callable[[str, str], None]

# 中文可用字体候选（macOS 优先，Linux 兜底）
_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Songti.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def _run(cmd: list[str], timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {proc.stderr.strip()[-400:]}")


def _has_audio(path: str) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a", "-show_entries",
         "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=60, check=False,
    )
    return "audio" in proc.stdout


def _load_font(size: int):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def _subtitle_pngs(ir: EditingIR, out_dir: Path) -> list[tuple[Path, float, float]]:
    """每条字幕渲染成一张与成片同尺寸的透明 PNG。无字幕或无字体返回空列表。"""
    items = sorted(
        (s for t in ir.tracks if isinstance(t, SubtitleTrack) for s in t.items),
        key=lambda s: s.timeline_start,
    )
    if not items:
        return []

    from PIL import Image, ImageDraw

    w, h = ir.project.resolution.width, ir.project.resolution.height
    font = _load_font(max(round(h * 0.05), 16))
    if font is None:
        return []

    results = []
    for i, sub in enumerate(items, start=1):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        box = draw.textbbox((0, 0), sub.content, font=font)
        x = (w - (box[2] - box[0])) // 2
        y = h - round(h * 0.12)
        for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3)):  # 描边+投影，保证亮背景可读
            draw.text((x + dx, y + dy), sub.content, font=font, fill=(0, 0, 0, 130))
        draw.text((x, y), sub.content, font=font, fill=(255, 255, 255, 255))
        png = out_dir / f"sub_{i}.png"
        img.save(png)
        results.append((png, sub.timeline_start, sub.timeline_end))
    return results


def render_video(
    ir: EditingIR,
    out_dir: Path,
    *,
    filename: str | None = None,
    burn_subtitles: bool = True,
    progress: Progress | None = None,
) -> dict:
    """按 IR 主视频轨渲染 mp4。返回 {video, duration, subtitles_burned, clips}。"""

    def emit(step: str, detail: str = "") -> None:
        if progress:
            progress(step, detail)

    video_tracks = [t for t in ir.tracks if isinstance(t, VideoTrack)]
    if not video_tracks or not min(video_tracks, key=lambda t: t.index).items:
        raise ValueError("IR 没有可渲染的视频片段")
    clips = min(video_tracks, key=lambda t: t.index).items

    out_dir.mkdir(parents=True, exist_ok=True)
    seg_dir = out_dir / "segments"
    seg_dir.mkdir(exist_ok=True)
    src_map = {s.id: s for s in ir.sources}
    w, h = ir.project.resolution.width, ir.project.resolution.height
    fps = ir.project.fps

    # 1) 各片段 trim 并统一到成片规格（含统一音轨，无音轨补静音，保证 concat 一致）
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p"
    )
    seg_paths = []
    for i, clip in enumerate(clips, start=1):
        src = src_map[clip.source_id]
        dur = clip.trim.end - clip.trim.start
        seg = seg_dir / f"seg_{i:03d}.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-ss", str(clip.trim.start), "-to", str(clip.trim.end), "-i", src.path]
        if _has_audio(src.path):
            cmd += ["-map", "0:v:0", "-map", "0:a:0"]
        else:
            cmd += ["-f", "lavfi", "-t", str(dur), "-i", "anullsrc=r=48000:cl=stereo",
                    "-map", "0:v:0", "-map", "1:a:0"]
        cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(seg)]
        _run(cmd)
        seg_paths.append(seg)
        emit("segment", f"{i}/{len(clips)} {Path(src.path).name}")

    # 2) concat（片段规格一致，直接流复制）
    concat_list = seg_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{p}'\n" for p in seg_paths), encoding="utf-8")
    merged = seg_dir / "merged.mp4"
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
          "-i", str(concat_list), "-c", "copy", str(merged)])
    emit("concat", f"{len(seg_paths)} 个片段合并完成")

    # 3) 字幕烧录（overlay 按时间段叠加 PNG）
    out_path = out_dir / (filename or f"{ir.project.name}.mp4")
    subtitles_burned = False
    overlays = _subtitle_pngs(ir, seg_dir) if burn_subtitles else []
    if overlays:
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(merged)]
        for png, _, _ in overlays:
            cmd += ["-i", str(png)]
        chains, prev = [], "0:v"
        for i, (_, start, end) in enumerate(overlays, start=1):
            label = "vout" if i == len(overlays) else f"v{i}"
            chains.append(f"[{prev}][{i}:v]overlay=enable='between(t,{start},{end})'[{label}]")
            prev = label
        cmd += ["-filter_complex", ";".join(chains), "-map", "[vout]", "-map", "0:a",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "copy", str(out_path)]
        _run(cmd)
        subtitles_burned = True
        emit("subtitles", f"{len(overlays)} 条字幕烧录完成")
    else:
        merged.replace(out_path)

    emit("done", str(out_path))
    return {
        "video": str(out_path),
        "duration": timeline_duration(ir),
        "subtitles_burned": subtitles_burned,
        "clips": len(clips),
    }
