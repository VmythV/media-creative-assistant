"""Editing IR v0.1：视频编辑中间表示（设计文档第 5 节）。

设计规则：
- sources 与 tracks 分离，clip 只引用 source_id。
- 每个 clip 带 role 与 reason（可解释性）。
- transition/effect/audio track 在枚举中预留但校验器拒绝（防止模型幻觉产出未实现能力）。
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

IR_VERSION = "0.1"

ClipRole = Literal["opening", "build", "climax", "ending", "broll"]


class Resolution(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ProjectSettings(BaseModel):
    name: str = Field(min_length=1)
    fps: float = Field(gt=0)
    resolution: Resolution


class Source(BaseModel):
    id: str = Field(min_length=1)
    path: str
    duration: float = Field(gt=0)


class Trim(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @model_validator(mode="after")
    def check_order(self) -> "Trim":
        if self.start >= self.end:
            raise ValueError(f"trim.start ({self.start}) 必须小于 trim.end ({self.end})")
        return self


class Clip(BaseModel):
    type: Literal["clip"] = "clip"
    source_id: str
    trim: Trim
    role: ClipRole
    reason: str = ""


class Subtitle(BaseModel):
    type: Literal["subtitle"] = "subtitle"
    content: str = Field(min_length=1)
    timeline_start: float = Field(ge=0)
    timeline_end: float = Field(gt=0)

    @model_validator(mode="after")
    def check_order(self) -> "Subtitle":
        if self.timeline_start >= self.timeline_end:
            raise ValueError("字幕 timeline_start 必须小于 timeline_end")
        return self


class VideoTrack(BaseModel):
    type: Literal["video"] = "video"
    index: int = Field(ge=1)
    items: list[Clip] = []


class SubtitleTrack(BaseModel):
    type: Literal["subtitle"] = "subtitle"
    index: int = Field(ge=1)
    items: list[Subtitle] = []


class EditingIR(BaseModel):
    version: str
    project: ProjectSettings
    sources: list[Source] = []
    tracks: list[VideoTrack | SubtitleTrack] = []
    render: None = None  # v0.1 恒为 null，schema 预留

    @field_validator("version")
    @classmethod
    def check_version(cls, v: str) -> str:
        if v != IR_VERSION:
            raise ValueError(f"不支持的 IR 版本: {v}（当前支持 {IR_VERSION}）")
        return v


class IRValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_ir(data: dict, *, check_paths: bool = True) -> EditingIR:
    """结构校验 + 语义校验。失败抛 IRValidationError（含全部错误，便于模型重试）。"""
    try:
        ir = EditingIR.model_validate(data)
    except Exception as e:
        raise IRValidationError([f"结构校验失败: {e}"]) from e

    errors: list[str] = []
    source_map = {s.id: s for s in ir.sources}
    if len(source_map) != len(ir.sources):
        errors.append("sources 中存在重复 id")

    seen_track_keys = set()
    for track in ir.tracks:
        key = (track.type, track.index)
        if key in seen_track_keys:
            errors.append(f"轨道重复: {track.type}#{track.index}")
        seen_track_keys.add(key)

    for track in ir.tracks:
        if track.type == "video":
            for i, clip in enumerate(track.items):
                src = source_map.get(clip.source_id)
                if src is None:
                    errors.append(f"video#{track.index} 第 {i + 1} 个片段引用了不存在的 source: {clip.source_id}")
                    continue
                if clip.trim.end > src.duration + 0.01:
                    errors.append(
                        f"video#{track.index} 第 {i + 1} 个片段 trim.end ({clip.trim.end}) 超出素材时长 ({src.duration})"
                    )
        elif track.type == "subtitle":
            items = sorted(track.items, key=lambda s: s.timeline_start)
            for a, b in zip(items, items[1:]):
                if a.timeline_end > b.timeline_start + 0.001:
                    errors.append(
                        f"subtitle#{track.index} 字幕时间重叠: [{a.timeline_start}-{a.timeline_end}] 与 [{b.timeline_start}-{b.timeline_end}]"
                    )

    if check_paths:
        for s in ir.sources:
            if not Path(s.path).is_file():
                errors.append(f"素材文件不存在: {s.path}")

    if errors:
        raise IRValidationError(errors)
    return ir


def timeline_duration(ir: EditingIR) -> float:
    """主视频轨（index 最小）总时长。"""
    video_tracks = [t for t in ir.tracks if t.type == "video"]
    if not video_tracks:
        return 0.0
    main = min(video_tracks, key=lambda t: t.index)
    return round(sum(c.trim.end - c.trim.start for c in main.items), 3)
