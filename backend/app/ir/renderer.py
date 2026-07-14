"""IR → mp4 成片渲染（设计文档 §9.2）。

约束：本机 ffmpeg 可能是精简编译（无 libass/drawtext），字幕烧录用
Pillow 渲染透明 PNG 后按时间段 overlay 实现，只依赖 overlay 滤镜。
"""

import subprocess
from pathlib import Path
from typing import Callable

from app.ir.schema import AudioTrack, EditingIR, SubtitleTrack, VideoTrack, timeline_duration

Progress = Callable[[str, str], None]

# 中文可用字体候选（macOS 优先，Linux 兜底）；按字体族分列（v0.5 样式）
_FONT_CANDIDATES = {
    "sans": [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ],
    "serif": [
        "/System/Library/Fonts/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
    ],
}


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


def _load_font(size: int, family: str = "sans"):
    from PIL import ImageFont

    for path in _FONT_CANDIDATES.get(family, []) + _FONT_CANDIDATES["sans"]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return None


def _hex_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _effective_spec(ir: EditingIR) -> tuple[int, int, str]:
    """交付规格（v0.4）：ir.render 优先，缺省按时间线规格 + pad（兼容旧行为）。"""
    if ir.render is not None:
        return ir.render.width, ir.render.height, ir.render.fill
    return ir.project.resolution.width, ir.project.resolution.height, "pad"


def _compose_graph(w: int, h: int, fill: str, fps: float) -> str:
    """单片段归一化滤镜图：目标画幅 + 构图策略（设计文档 phase2 §2）。"""
    if fill == "crop":
        return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},fps={fps},format=yuv420p[vout]")
    if fill == "blur":
        return (
            f"[0:v]split[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},boxblur=20:2[bgb];"
            f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,fps={fps},format=yuv420p[vout]"
        )
    return (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p[vout]")


def _subtitle_pngs(ir: EditingIR, out_dir: Path) -> list[tuple[Path, float, float]]:
    """每条字幕渲染成一张与成片同尺寸的透明 PNG。无字幕或无字体返回空列表。

    v0.5：按字幕轨 style 绘制（位置/颜色/字号/描边/底条/字体族），缺省即历史行为。
    """
    from app.ir.schema import SubtitleStyle

    tracks = [t for t in ir.tracks if isinstance(t, SubtitleTrack)]
    items = sorted(((s, t.style) for t in tracks for s in t.items),
                   key=lambda pair: pair[0].timeline_start)
    if not items:
        return []

    from PIL import Image, ImageDraw

    w, h, _ = _effective_spec(ir)
    results = []
    for i, (sub, style) in enumerate(items, start=1):
        st = style or SubtitleStyle()
        size = max(round(h * st.size_ratio), 16)
        font = _load_font(size, st.font)
        if font is None:
            return []
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        box = draw.textbbox((0, 0), sub.content, font=font)
        if box[2] - box[0] > w * 0.92:  # 窄画幅长字幕自动缩字适配宽度
            size = max(round(size * w * 0.92 / (box[2] - box[0])), 14)
            font = _load_font(size, st.font)
            box = draw.textbbox((0, 0), sub.content, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        x = (w - tw) // 2
        if st.position == "top":
            y = round(h * 0.06)
        elif st.position == "center":
            y = (h - th) // 2
        else:
            y = h - round(h * 0.12)
        if st.background:  # 半透明底条（圆角），留呼吸边距
            pad = max(round(h * 0.012), 6)
            draw.rounded_rectangle(
                (x - pad * 2, y + box[1] - pad, x + tw + pad * 2, y + box[3] + pad),
                radius=pad, fill=(0, 0, 0, 150),
            )
        if st.outline:
            for dx, dy in ((-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3)):  # 描边+投影，保证亮背景可读
                draw.text((x + dx, y + dy), sub.content, font=font, fill=(0, 0, 0, 130))
        draw.text((x, y), sub.content, font=font, fill=(*_hex_rgb(st.color), 255))
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
    w, h, fill = _effective_spec(ir)
    fps = ir.project.fps

    # 1) 各片段 trim 并统一到交付规格（含统一音轨，无音轨补静音，保证 concat 一致）
    graph = _compose_graph(w, h, fill, fps)
    seg_paths = []
    for i, clip in enumerate(clips, start=1):
        src = src_map[clip.source_id]
        dur = clip.trim.end - clip.trim.start
        seg = seg_dir / f"seg_{i:03d}.mp4"
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-ss", str(clip.trim.start), "-to", str(clip.trim.end), "-i", src.path]
        if _has_audio(src.path):
            cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "0:a:0"]
        else:
            cmd += ["-f", "lavfi", "-t", str(dur), "-i", "anullsrc=r=48000:cl=stereo",
                    "-filter_complex", graph, "-map", "[vout]", "-map", "1:a:0"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(seg)]
        _run(cmd)
        seg_paths.append(seg)
        emit("segment", f"{i}/{len(clips)} {Path(src.path).name}")

    # 2) 拼接：无转场走 concat 流复制快路径；有转场单次 filter_complex 链式折叠（设计文档 §12）
    merged = seg_dir / "merged.mp4"
    transitions = [c.transition for c in clips]
    if not any(transitions[1:]):
        concat_list = seg_dir / "concat.txt"
        concat_list.write_text("".join(f"file '{p}'\n" for p in seg_paths), encoding="utf-8")
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(concat_list), "-c", "copy", str(merged)])
        emit("concat", f"{len(seg_paths)} 个片段合并完成")
    else:
        durs = [c.trim.end - c.trim.start for c in clips]
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for p in seg_paths:
            cmd += ["-i", str(p)]
        # xfade 要求两输入 timebase 一致，而 concat 滤镜输出 1/1000000：全部视频输入统一 settb
        chains: list[str] = [f"[{i}:v]settb=AVTB[vin{i}]" for i in range(len(clips))]
        vprev, aprev = "[vin0]", "[0:a]"
        out_len = durs[0]
        for i in range(1, len(clips)):
            t = transitions[i]
            vlab, alab = f"[v{i}]", f"[a{i}]"
            if t is not None:
                offset = round(out_len - t.duration, 3)
                chains.append(
                    f"{vprev}[vin{i}]xfade=transition={t.type}"
                    f":duration={t.duration}:offset={offset}{vlab}"
                )
                chains.append(f"{aprev}[{i}:a]acrossfade=d={t.duration}{alab}")
                out_len += durs[i] - t.duration
            else:
                chains.append(f"{vprev}[vin{i}]concat=n=2:v=1:a=0{vlab}")
                chains.append(f"{aprev}[{i}:a]concat=n=2:v=0:a=1{alab}")
                out_len += durs[i]
            vprev, aprev = vlab, alab
        cmd += ["-filter_complex", ";".join(chains), "-map", vprev, "-map", aprev,
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", str(merged)]
        _run(cmd)
        n_trans = sum(1 for t in transitions[1:] if t)
        emit("concat", f"{len(seg_paths)} 个片段合并完成（含 {n_trans} 处转场）")

    # 3) 配乐混音（视频流 copy，仅重编音频；设计文档 §11）
    music = next(
        (m for t in ir.tracks if isinstance(t, AudioTrack) for m in t.items), None
    )
    if music is not None:
        total = timeline_duration(ir)
        src = src_map[music.source_id]
        mixed = seg_dir / "mixed.mp4"
        fade_out_start = max(total - music.fade_out, 0)
        bgm = (
            f"[1:a]atrim=0:{total},volume={music.gain_db}dB,"
            f"afade=t=in:st=0:d={music.fade_in},"
            f"afade=t=out:st={fade_out_start}:d={music.fade_out}[bgm];"
            f"[0:a][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(merged)]
        if music.loop:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", src.path, "-filter_complex", bgm,
                "-map", "0:v", "-map", "[aout]", "-c:v", "copy",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", str(mixed)]
        _run(cmd)
        merged = mixed
        emit("music", f"配乐混音完成（{Path(src.path).name}，{music.gain_db}dB）")

    # 4) 字幕烧录（overlay 按时间段叠加 PNG）
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
        "resolution": f"{w}x{h}",
        "subtitles_burned": subtitles_burned,
        "clips": len(clips),
        "transitions": sum(1 for c in clips if c.transition),
        "music": Path(src_map[music.source_id].path).name if music else None,
    }
