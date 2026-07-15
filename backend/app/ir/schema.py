"""Editing IR：视频编辑中间表示（设计文档第 5 节；§11 音频轨、§12 转场）。

设计规则：
- sources 与 tracks 分离，clip 只引用 source_id。
- 每个 clip 带 role 与 reason（可解释性）。
- effect 在枚举中预留但校验器拒绝（防止模型幻觉产出未实现能力）。
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

IR_VERSION = "0.6"
# 0.1：无音频轨；0.2：无转场；0.3：无交付规格；0.4：无字幕样式；0.5：无变速
SUPPORTED_VERSIONS = {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6"}

ClipRole = Literal["opening", "build", "climax", "ending", "broll"]

# ffmpeg xfade 滤镜类型白名单（本机精简编译已确认含 xfade/acrossfade）
TransitionType = Literal[
    "fade", "fadeblack", "fadewhite", "dissolve",
    "wipeleft", "wiperight", "slideleft", "slideright",
    "circleopen", "circleclose",
]
TRANSITION_TYPES: frozenset[str] = frozenset(TransitionType.__args__)


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


class Transition(BaseModel):
    """与前一片段之间的转场（转场进入本片段，v0.3）。重叠消耗两侧素材。"""

    type: TransitionType
    duration: float = Field(default=0.5, gt=0, le=2.0)


class Clip(BaseModel):
    type: Literal["clip"] = "clip"
    source_id: str
    trim: Trim
    role: ClipRole
    reason: str = ""
    transition: Transition | None = None
    # 变速（v0.6）：>1 快放、<1 慢动作；时间线时长 = 素材段长 / speed
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    # 主体感知裁切焦点（M28）：0 最左 / 0.5 居中 / 1 最右；仅 fill=crop 时生效，None 即居中
    crop_focus: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def timeline_len(self) -> float:
        """片段在时间线上的时长（考虑变速）。"""
        return (self.trim.end - self.trim.start) / self.speed


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


class MusicClip(BaseModel):
    """配乐（v0.2）：铺满整条时间线，loop 截齐，首尾淡入淡出。"""

    type: Literal["music"] = "music"
    source_id: str
    gain_db: float = Field(default=-16.0, ge=-60, le=12)
    fade_in: float = Field(default=1.0, ge=0)
    fade_out: float = Field(default=2.0, ge=0)
    loop: bool = True


class VideoTrack(BaseModel):
    type: Literal["video"] = "video"
    index: int = Field(ge=1)
    items: list[Clip] = []


class AudioTrack(BaseModel):
    type: Literal["audio"] = "audio"
    index: int = Field(ge=1)
    items: list[MusicClip] = []


class SubtitleStyle(BaseModel):
    """字幕样式（v0.5）：预设名仅作展示，渲染只读具体字段；缺省即历史行为。"""

    preset: str = "default"
    position: Literal["bottom", "top", "center"] = "bottom"
    size_ratio: float = Field(default=0.05, gt=0.01, le=0.15)  # 字号 / 画面高
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    outline: bool = True     # 描边 + 投影
    background: bool = False  # 半透明底条
    font: Literal["sans", "serif"] = "sans"


# 预设 → 具体字段（确定性展开，API/对话写入时使用）
SUBTITLE_PRESETS: dict[str, dict] = {
    "default": {},
    "elegant": {"font": "serif", "size_ratio": 0.045, "color": "#FFF8E7"},
    "bold": {"size_ratio": 0.065, "color": "#FFD400", "background": True},
    "minimal": {"size_ratio": 0.04, "outline": False, "background": True},
}


class SubtitleTrack(BaseModel):
    type: Literal["subtitle"] = "subtitle"
    index: int = Field(ge=1)
    items: list[Subtitle] = []
    style: SubtitleStyle | None = None  # v0.5：None 即默认样式


class RenderSpec(BaseModel):
    """交付规格（v0.4）：与时间线规格（project.resolution）解耦；缺省即按时间线规格输出。

    fill：目标画幅与素材不符时的构图策略——pad 加黑边（兼容旧行为）/
    crop 裁满 / blur 模糊背景居中（竖屏推荐）。
    """

    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fill: Literal["pad", "crop", "blur"] = "blur"
    quality: Literal["draft", "final"] = "final"  # M20：draft 快速出样片

    @model_validator(mode="after")
    def check_even(self) -> "RenderSpec":
        if self.width % 2 or self.height % 2:
            raise ValueError(f"交付分辨率必须为偶数（libx264 要求）: {self.width}x{self.height}")
        return self


class EditingIR(BaseModel):
    version: str
    project: ProjectSettings
    sources: list[Source] = []
    tracks: list[VideoTrack | SubtitleTrack | AudioTrack] = []
    render: RenderSpec | None = None  # 交付规格（v0.4 启用）

    @field_validator("version")
    @classmethod
    def check_version(cls, v: str) -> str:
        if v not in SUPPORTED_VERSIONS:
            raise ValueError(f"不支持的 IR 版本: {v}（当前支持 {sorted(SUPPORTED_VERSIONS)}）")
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
            for i, clip in enumerate(track.items):
                if i == 0 and clip.transition is not None:
                    errors.append(f"video#{track.index} 首个片段不能有 transition（转场语义为与前一片段之间）")
                # 转场消耗两侧重叠：转入 + 转出必须小于片段的时间线时长（变速后）
                t_in = clip.transition.duration if clip.transition else 0.0
                nxt = track.items[i + 1] if i + 1 < len(track.items) else None
                t_out = nxt.transition.duration if nxt and nxt.transition else 0.0
                tl_len = clip.timeline_len
                if t_in + t_out >= tl_len:
                    errors.append(
                        f"video#{track.index} 第 {i + 1} 个片段时间线时长 {tl_len:.2f}s 不足以承载"
                        f"转入 {t_in}s + 转出 {t_out}s 的转场重叠"
                    )
        elif track.type == "audio":
            if len(track.items) > 1:
                errors.append(f"audio#{track.index} 仅支持单条配乐（MVP 限制），当前 {len(track.items)} 条")
            for m in track.items:
                if m.source_id not in source_map:
                    errors.append(f"audio#{track.index} 配乐引用了不存在的 source: {m.source_id}")
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
    """主视频轨（index 最小）总时长。转场消耗重叠：总长 = Σ片段 − Σ转场。"""
    video_tracks = [t for t in ir.tracks if t.type == "video"]
    if not video_tracks:
        return 0.0
    main = min(video_tracks, key=lambda t: t.index)
    total = sum(c.timeline_len for c in main.items)  # 变速后的时间线时长
    total -= sum(c.transition.duration for c in main.items if c.transition)
    return round(total, 3)
