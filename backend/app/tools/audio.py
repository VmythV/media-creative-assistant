"""audio 工具组：faster-whisper 本地语音转写（支持中文）。"""

from pathlib import Path

from app.config import settings
from app.tools.registry import registry

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel(settings.whisper_model, device="auto", compute_type="auto")
    return _model


@registry.register(
    name="transcribe_audio",
    description="语音转写（faster-whisper 本地运行，支持中文），输出带时间戳的分段文本。",
    parameters={
        "type": "object",
        "properties": {
            "wav_path": {"type": "string", "description": "wav 文件绝对路径"},
            "language": {"type": "string", "description": "语言代码，如 zh/en；留空自动检测"},
        },
        "required": ["wav_path"],
    },
)
def transcribe_audio(wav_path: str, language: str | None = None) -> dict:
    if not Path(wav_path).is_file():
        raise FileNotFoundError(f"文件不存在: {wav_path}")
    model = _get_model()
    segments, info = model.transcribe(wav_path, language=language, vad_filter=True)
    seg_list = [
        {"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()} for s in segments
    ]
    return {
        "language": info.language,
        "segments": seg_list,
        "text": "".join(s["text"] for s in seg_list),
    }
