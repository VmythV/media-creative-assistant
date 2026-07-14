"""风格学习（M18，phase2-roadmap §7）：参考视频 → 节奏画像 → 生成注入。

画像提取是纯确定性统计（镜头检测复用现有工具），不经模型；
画像文本本身就是注入体，存 Memory kind=business（source=style）。
"""

import logging
import statistics
from pathlib import Path

from app.memory import get_memory_provider
from app.tools.media import probe_media
from app.tools.shots import detect_shots

logger = logging.getLogger("mca.style")

STYLE_SOURCE = "style"


def _pace(avg_shot: float) -> str:
    if avg_shot < 2.0:
        return "快"
    if avg_shot < 4.5:
        return "中等"
    return "舒缓"


def learn_style(path: str, name: str | None = None) -> dict:
    """分析参考视频节奏，产出画像并存入 Memory（kind=business）。返回画像 dict。"""
    file = Path(path).expanduser()
    if not file.is_file():
        raise ValueError(f"参考视频不存在: {path}")
    meta = probe_media(str(file))
    if not meta.get("video"):
        raise ValueError("参考文件不含视频流")
    duration = meta["duration"]
    shots = detect_shots(str(file))["shots"]
    if not shots:
        raise ValueError("未检测到镜头")

    lens = [s["end"] - s["start"] for s in shots]
    avg = sum(lens) / len(lens)
    med = statistics.median(lens)
    p90 = sorted(lens)[max(round(len(lens) * 0.9) - 1, 0)]
    cuts_per_min = (len(shots) - 1) / duration * 60 if duration else 0.0
    pace = _pace(avg)
    style_name = name or file.stem

    profile = {
        "name": style_name,
        "source": file.name,
        "duration": round(duration, 1),
        "shots": len(shots),
        "cuts_per_min": round(cuts_per_min, 1),
        "avg_shot": round(avg, 2),
        "median_shot": round(med, 2),
        "p90_shot": round(p90, 2),
        "pace": pace,
    }
    # 画像文本即注入体：生成方案时片段时长/节奏向其靠拢
    text = (
        f"风格「{style_name}」：节奏{pace}（平均镜头 {avg:.1f}s，每分钟 {cuts_per_min:.1f} 次切换），"
        f"镜头时长中位 {med:.1f}s、90 分位 {p90:.1f}s；"
        f"学自 {file.name}（{duration:.0f}s，{len(shots)} 个镜头）"
    )
    memory = get_memory_provider()
    # 同名画像覆盖：删除旧条目再写入
    for m in memory.list("business"):
        if m["source"] == STYLE_SOURCE and f"「{style_name}」" in m["content"]:
            memory.delete(m["id"])
    memory.add("business", text, source=STYLE_SOURCE)
    profile["text"] = text
    return profile


def list_styles() -> list[dict]:
    """已学风格画像列表（Memory kind=business, source=style）。"""
    return [m for m in get_memory_provider().list("business") if m["source"] == STYLE_SOURCE]


def find_style(name: str) -> str | None:
    """按名找画像文本；找不到返回 None。"""
    for m in list_styles():
        if f"「{name}」" in m["content"]:
            return m["content"]
    return None
