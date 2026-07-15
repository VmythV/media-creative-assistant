"""media 工具组：FFmpeg / ffprobe 相关工具。"""

import json
import subprocess
from fractions import Fraction
from pathlib import Path

from app.tools.registry import registry


def _run(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)


def _asset_cache_dir(path: str) -> Path:
    from app.config import settings
    from app.store.hashing import content_hash

    d = settings.cache_dir / content_hash(path)
    d.mkdir(parents=True, exist_ok=True)
    return d


@registry.register(
    name="probe_media",
    description="提取媒体文件元数据：时长、分辨率、帧率、编码、音轨信息。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "媒体文件绝对路径"}},
        "required": ["path"],
    },
)
def probe_media(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    proc = _run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(file)]
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {proc.stderr.strip()[:300]}")
    raw = json.loads(proc.stdout)

    video = next((s for s in raw.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in raw.get("streams", []) if s.get("codec_type") == "audio"), None)
    fmt = raw.get("format", {})

    fps = None
    if video and video.get("avg_frame_rate") not in (None, "0/0"):
        try:
            fps = round(float(Fraction(video["avg_frame_rate"])), 3)
        except (ValueError, ZeroDivisionError):
            fps = None

    return {
        "path": str(file),
        "size_bytes": int(fmt.get("size", 0)),
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "container": fmt.get("format_name"),
        "video": {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": fps,
        }
        if video
        else None,
        "audio": {
            "codec": audio.get("codec_name"),
            "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
            "channels": audio.get("channels"),
        }
        if audio
        else None,
    }


@registry.register(
    name="extract_audio",
    description="从视频中提取音频为 16kHz 单声道 wav（适配语音识别），输出到素材缓存目录。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "视频文件绝对路径"}},
        "required": ["path"],
    },
)
def extract_audio(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    out = _asset_cache_dir(path) / "audio.wav"
    if not out.exists():
        proc = _run(["ffmpeg", "-y", "-v", "error", "-i", str(file), "-vn", "-ac", "1", "-ar", "16000", str(out)])
        if proc.returncode != 0:
            raise RuntimeError(f"音频提取失败: {proc.stderr.strip()[:300]}")
    return {"wav_path": str(out)}


@registry.register(
    name="sample_frames",
    description="按时间点从视频抽取关键帧（jpg），输出到素材缓存目录。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "视频文件绝对路径"},
            "timestamps": {"type": "array", "items": {"type": "number"}, "description": "抽帧时间点（秒）"},
        },
        "required": ["path", "timestamps"],
    },
)
def sample_frames(path: str, timestamps: list[float]) -> dict:
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    frames_dir = _asset_cache_dir(path) / "frames"
    frames_dir.mkdir(exist_ok=True)
    frames = []
    for ts in timestamps:
        out = frames_dir / f"{ts:.2f}.jpg"
        if not out.exists():
            proc = _run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.3f}", "-i", str(file),
                 "-frames:v", "1", "-q:v", "3", "-vf", "scale='min(1280,iw)':-2", str(out)]
            )
            if proc.returncode != 0 or not out.exists():
                continue  # 个别时间点抽帧失败（如超出时长）不阻断整体
        frames.append({"timestamp": ts, "image_path": str(out)})
    if not frames:
        raise RuntimeError("所有时间点抽帧均失败")
    return {"frames": frames}


@registry.register(
    name="detect_audio_events",
    description="检测音频中的静音区间与整体响度（FFmpeg silencedetect/volumedetect 启发式）。",
    parameters={
        "type": "object",
        "properties": {"wav_path": {"type": "string", "description": "wav 文件绝对路径"}},
        "required": ["wav_path"],
    },
)
def detect_audio_events(wav_path: str) -> dict:
    file = Path(wav_path)
    if not file.is_file():
        raise FileNotFoundError(f"文件不存在: {wav_path}")
    proc = _run(
        ["ffmpeg", "-v", "info", "-i", str(file),
         "-af", "silencedetect=noise=-35dB:d=0.8,volumedetect", "-f", "null", "-"]
    )
    stderr = proc.stderr
    silences: list[dict] = []
    start = None
    mean_volume = max_volume = None
    for line in stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.rsplit("silence_start:", 1)[1].strip())
        elif "silence_end:" in line and start is not None:
            end = float(line.rsplit("silence_end:", 1)[1].split("|")[0].strip())
            silences.append({"start": round(start, 3), "end": round(end, 3)})
            start = None
        elif "mean_volume:" in line:
            mean_volume = float(line.rsplit("mean_volume:", 1)[1].replace("dB", "").strip())
        elif "max_volume:" in line:
            max_volume = float(line.rsplit("max_volume:", 1)[1].replace("dB", "").strip())
    return {"silences": silences, "mean_volume_db": mean_volume, "max_volume_db": max_volume}


IMAGE_CLIP_DURATION = 4.0
IMAGE_CLIP_FPS = 25
IMAGE_CLIP_SIZE = (1920, 1080)


@registry.register(
    name="image_to_clip",
    description="把照片转成带 Ken Burns 缓慢推近的视频片段（默认 4 秒 1080p），供分析管线与剪辑使用。",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "图片文件绝对路径（jpg/png/webp/heic）"},
            "duration": {"type": "number", "description": "片段时长（秒），默认 4"},
        },
        "required": ["path"],
    },
)
def image_to_clip(path: str, duration: float = IMAGE_CLIP_DURATION) -> dict:
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(f"文件不存在: {path}")

    from app.config import settings
    from app.store.hashing import content_hash

    out_dir = settings.data_dir / "image_clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{file.stem}_{content_hash(file)[:8]}.mp4"
    if out.exists():  # 同一图片幂等复用
        return {"clip_path": str(out), "duration": duration, "cached": True}

    from PIL import Image, ImageOps

    try:  # HEIC 支持（可选依赖）
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass

    # EXIF 方向烘焙进像素（ffmpeg 各版本 EXIF 自动旋转行为不一，不依赖它）
    import tempfile

    with Image.open(file) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        width, height = img.size
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            baked = Path(tmp.name)
        img.save(baked, quality=92)

    try:
        w, h = IMAGE_CLIP_SIZE
        frames = round(duration * IMAGE_CLIP_FPS)
        zoom = (
            f"zoompan=z='1+0.06*on/{frames}':x='iw/2-iw/(2*zoom)':y='ih/2-ih/(2*zoom)'"
            f":d={frames}:s={w}x{h}:fps={IMAGE_CLIP_FPS}"
        )
        common = ["-frames:v", str(frames), "-c:v", "libx264", "-preset", "medium",
                  "-crf", "18", "-pix_fmt", "yuv420p", str(out)]
        if width >= height:
            # 横构图：放大填满 16:9 后中心裁切
            vf = f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},{zoom}"
            proc = _run(["ffmpeg", "-y", "-v", "error", "-i", str(baked), "-vf", vf, *common])
        else:
            # 竖构图：模糊放大背景 + 原图等高居中
            fc = (
                f"[0:v]split[bg][fg];"
                f"[bg]scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
                f"crop={w * 2}:{h * 2},gblur=sigma=40,eq=brightness=-0.08[b];"
                f"[fg]scale=-2:{h * 2}[f];"
                f"[b][f]overlay=(W-w)/2:(H-h)/2,{zoom}"
            )
            proc = _run(["ffmpeg", "-y", "-v", "error", "-i", str(baked), "-filter_complex", fc, *common])
        if proc.returncode != 0:
            raise RuntimeError(f"图片转片段失败: {proc.stderr.strip()[:300]}")
    finally:
        baked.unlink(missing_ok=True)

    return {"clip_path": str(out), "duration": duration, "cached": False}


def generate_title_clip(text: str, *, subtitle: str = "", duration: float = 2.5,
                        width: int = 1920, height: int = 1080, fps: float = 25.0,
                        background: str = "#000000", color: str = "#FFFFFF") -> dict:
    """标题卡（M26）：纯色背景 + 居中标题/副标题 → 定长静止视频。内容哈希缓存幂等。

    本机 ffmpeg 精简编译无 drawtext，故用 Pillow 渲染文字 PNG 再转视频（同字幕方案）。
    """
    import hashlib

    from app.config import settings
    from app.ir.renderer import _hex_rgb, _load_font

    key = hashlib.sha1(
        f"{text}|{subtitle}|{duration}|{width}x{height}|{fps}|{background}|{color}".encode()
    ).hexdigest()[:12]
    out_dir = settings.data_dir / "title_clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"title_{key}.mp4"
    if out.exists():
        return {"clip_path": str(out), "duration": duration, "cached": True}

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), _hex_rgb(background))
    draw = ImageDraw.Draw(img)
    title_font = _load_font(max(round(height * 0.09), 20), "serif")
    sub_font = _load_font(max(round(height * 0.045), 14), "sans")
    if title_font is None:
        raise RuntimeError("无可用中文字体，无法生成标题卡")

    def _centered(txt, font, cy):
        box = draw.textbbox((0, 0), txt, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        draw.text(((width - tw) // 2 - box[0], cy - th // 2 - box[1]), txt, font=font,
                  fill=(*_hex_rgb(color), 255))

    if subtitle:
        _centered(text, title_font, round(height * 0.44))
        _centered(subtitle, sub_font, round(height * 0.56))
    else:
        _centered(text, title_font, height // 2)

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        png = Path(tmp.name)
    img.save(png)
    try:
        proc = _run([
            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", str(duration), "-i", str(png),
            "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", str(out),
        ])
        if proc.returncode != 0:
            raise RuntimeError(f"标题卡生成失败: {proc.stderr.strip()[:300]}")
    finally:
        png.unlink(missing_ok=True)
    return {"clip_path": str(out), "duration": duration, "cached": False}
