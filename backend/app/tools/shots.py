"""镜头边界检测（PySceneDetect）。"""

from pathlib import Path

from app.tools.registry import registry


@registry.register(
    name="detect_shots",
    description="检测视频镜头边界，返回每个镜头的起止时间。",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "视频文件绝对路径"}},
        "required": ["path"],
    },
)
def detect_shots(path: str) -> dict:
    if not Path(path).is_file():
        raise FileNotFoundError(f"文件不存在: {path}")
    from scenedetect import ContentDetector, detect

    scenes = detect(path, ContentDetector(threshold=27.0))
    shots = [
        {"index": i, "start": round(s.get_seconds(), 3), "end": round(e.get_seconds(), 3)}
        for i, (s, e) in enumerate(scenes)
    ]
    if not shots:
        # 无剪切点：整条视频视为单一镜头
        from app.tools.media import probe_media

        meta = probe_media(path)
        duration = meta["duration"] or 0.0
        shots = [{"index": 0, "start": 0.0, "end": round(duration, 3)}]
    return {"shots": shots}
