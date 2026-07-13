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
