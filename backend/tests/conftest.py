import os
import tempfile

# 必须在导入 app.* 之前设置：测试数据目录与真实 data/ 隔离
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="mca-test-"))

import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """用 FFmpeg 生成 5 秒带音频的测试视频。"""
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "quiet",
            "-f", "lavfi", "-i", "testsrc=duration=5:size=640x360:rate=25",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture()
def analyzed_asset(sample_video) -> int:
    """构造一个已分析状态的素材（get-or-create），返回 asset_id。"""
    from app.store.db import db_session
    from app.store.hashing import content_hash
    from app.store.models import AnalysisRecord, Asset

    with db_session() as db:
        asset = db.query(Asset).filter_by(path=str(sample_video)).first()
        if asset is None:
            asset = Asset(
                path=str(sample_video),
                filename=sample_video.name,
                content_hash=content_hash(sample_video),
                size_bytes=sample_video.stat().st_size,
                duration=5.0,
                width=640,
                height=360,
                fps=25.0,
                has_audio=1,
            )
            db.add(asset)
        asset.status = "analyzed"
        db.commit()
        chash = asset.content_hash
        for kind, payload in (
            ("shots", {"shots": [{"index": 0, "start": 0.0, "end": 5.0}]}),
            ("summary", {
                "category": "风景",
                "shot_count": 1,
                "highlights": [{"shot_index": 0, "start": 0.0, "end": 5.0, "score": 8.0,
                                "reason": "画面完整", "category": "风景", "suitable_roles": ["opening"]}],
                "has_speech": False,
                "vision_available": True,
            }),
        ):
            if not db.query(AnalysisRecord).filter_by(content_hash=chash, kind=kind, version="v1").first():
                db.add(AnalysisRecord(content_hash=chash, kind=kind, version="v1", payload=payload))
        db.commit()
        return asset.id
