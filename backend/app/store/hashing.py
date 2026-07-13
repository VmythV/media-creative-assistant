"""素材内容 hash：大文件不做全量 sha256，取 size + 首尾各 1MB。

用作分析缓存键：同一文件移动/改名后缓存仍命中。
"""

import hashlib
from pathlib import Path

_CHUNK = 1024 * 1024


def content_hash(path: str | Path) -> str:
    file = Path(path)
    size = file.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with file.open("rb") as f:
        h.update(f.read(_CHUNK))
        if size > 2 * _CHUNK:
            f.seek(-_CHUNK, 2)
            h.update(f.read(_CHUNK))
    return h.hexdigest()[:32]
