"""Shot-level storyboard structures and vertical-platform geometry rules.

A storyboard is the per-episode list of shots plus the timing plan that keeps
the episode inside its platform duration tier. This module owns:

- the :class:`Shot` / :class:`Lineage` data model (shot lineage is what makes
  partial regeneration possible: regen only touches ``drifted`` shots),
- the platform rule engine: 9:16 canvas geometry, the subtitle safe zone
  (expressed in canvas fractions), and duration-tier budgets.

The series container that wraps these models lives in :mod:`dramaser.series`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

#: Canvas resolution for the 9:16 vertical presets (douyin / channels-TV).
CANVAS_9_16 = (1080, 1920)

#: Duration tiers (seconds) that a single episode timeline may occupy.
DURATION_TIERS: dict[str, int] = {
    "15s": 15,
    "60s": 60,
    "90s": 90,
    "3min": 180,
}

#: Default consistency gate threshold for a locked shot.
DEFAULT_CONSISTENCY_THRESHOLD = 0.85


class ShotStatus(str, Enum):
    """Lifecycle of one shot's generated asset."""

    pending = "pending"
    locked = "locked"
    drifted = "drifted"


class Lineage(BaseModel):
    """Provenance of one generated shot asset.

    ``run_id`` / ``provider`` / ``params_hash`` identify the producing run so a
    later ``regen`` can decide whether an asset may be reused as-is; the judge
    score and status record the outcome of the consistency gate.
    """

    run_id: str = ""
    provider: str = ""
    params_hash: str = ""
    judge_score: float | None = None
    status: ShotStatus = ShotStatus.pending
    asset: str = ""

    def mark(self, score: float, threshold: float) -> None:
        """Apply the consistency gate to this lineage."""
        self.judge_score = round(float(score), 4)
        self.status = ShotStatus.locked if score >= threshold else ShotStatus.drifted


class Shot(BaseModel):
    """One storyboard shot: who is on screen, what is said, how it is framed."""

    id: str
    characters: list[str] = Field(default_factory=list)
    line: str = ""
    action: str = ""
    camera: str = "中景"
    duration_s: float = 5.0
    lineage: Lineage = Field(default_factory=Lineage)

    @property
    def episode_id(self) -> str:
        """``ep01_s03`` -> ``ep01``."""
        return self.id.rsplit("_s", 1)[0]


class SubtitleSafeZone(BaseModel):
    """Regions of the 9:16 canvas kept clear of platform UI, as height/width fractions."""

    top: float = 0.08
    bottom: float = 0.12
    right_rail: float = 0.10


class PlatformPreset(BaseModel):
    """Platform delivery rules attached to a series."""

    preset: str = "douyin_60s"
    aspect: str = "9:16"
    duration_tier: int = 60
    subtitle_safe_zone: SubtitleSafeZone = Field(default_factory=SubtitleSafeZone)


@dataclass(frozen=True)
class PlatformGeometry:
    """Derived geometry for one platform preset.

    All fractions are relative to the canvas; consumers convert them with
    :meth:`subtitle_transform_y` / :meth:`subtitle_max_line_width`.
    """

    width: int
    height: int
    safe: SubtitleSafeZone
    duration_tier: int

    @classmethod
    def from_preset(cls, preset: PlatformPreset) -> "PlatformGeometry":
        if preset.aspect != "9:16":
            raise ValueError(f"v0.1 仅支持 9:16 竖屏画布，收到 {preset.aspect!r}")
        width, height = CANVAS_9_16
        return cls(
            width=width,
            height=height,
            safe=preset.subtitle_safe_zone,
            duration_tier=preset.duration_tier,
        )

    @property
    def canvas_us(self) -> int:
        """Full-episode timeline budget in microseconds."""
        return int(self.duration_tier * 1_000_000)

    def subtitle_transform_y(self) -> float:
        """Vertical offset for the subtitle baseline, in half-canvas units.

        pyJianYingDraft expresses ``transform_y`` in units of half the canvas
        height (0 = centered, -1 = bottom edge). Placing the subtitle box so
        its center sits one ``safe.bottom`` fraction above the bottom edge
        gives ``-(1 - 2 * bottom)``; JianYing's own imported subtitles use
        -0.8, which corresponds to bottom = 0.10.
        """
        return -(1.0 - 2.0 * self.safe.bottom)

    def subtitle_max_line_width(self) -> float:
        """Fraction of canvas width a centered subtitle line may occupy."""
        return 1.0 - 2.0 * self.safe.right_rail


def plan_episode_timing(
    shots: list[Shot], geometry: PlatformGeometry
) -> list[tuple[int, int]]:
    """Chained ``(start_us, duration_us)`` timeranges, one per shot.

    Raises :class:`ValueError` when the episode exceeds its duration tier, so
    callers cannot build an over-budget timeline silently.
    """
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for shot in shots:
        duration = int(round(shot.duration_s * 1_000_000))
        if duration <= 0:
            raise ValueError(f"镜头 {shot.id} 的时长必须为正数")
        ranges.append((cursor, duration))
        cursor += duration
    if cursor > geometry.canvas_us:
        raise ValueError(
            f"分镜总时长 {cursor / 1_000_000:.1f}s 超出时长档 "
            f"{geometry.duration_tier}s，请拆分镜头或提高时长档"
        )
    return ranges


def episode_total_s(shots: list[Shot]) -> float:
    return sum(shot.duration_s for shot in shots)


def shots_by_episode(shots: list[Shot], episode_id: str) -> list[Shot]:
    return [shot for shot in shots if shot.episode_id == episode_id]


def resolve_asset_path(series_dir: Path, asset_ref: str) -> Path:
    """Resolve a lineage asset reference (absolute or series-relative)."""
    path = Path(asset_ref)
    return path if path.is_absolute() else series_dir / path
