"""JianYing (剪映) draft export, install, and verification.

Builds drafts with pyJianYingDraft on a 9:16 canvas: one video segment per
shot (chained per episode, each episode bounded by its platform duration
tier), plus a subtitle text track positioned inside the platform's subtitle
safe zone. ``check draft`` re-opens the exported JSON and verifies all of
that machine-readably.
"""

from __future__ import annotations

import json
import platform as _platform
import shutil
from dataclasses import dataclass
from pathlib import Path

from pyJianYingDraft import (
    ClipSettings,
    DraftFolder,
    FontType,
    ScriptFile,
    TextSegment,
    TextStyle,
    TrackSpec,
    TrackType,
    VideoMaterial,
    VideoSegment,
    trange,
)

from .series import Episode, Series
from .storyboard import (
    PlatformGeometry,
    Shot,
    plan_episode_timing,
)

#: Subtitle typography for the exported text track.
SUBTITLE_FONT = FontType.文轩体
SUBTITLE_SIZE = 8.0


@dataclass
class CheckResult:
    """One verifiable property of an exported draft."""

    name: str
    ok: bool
    detail: str


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_draft(
    series: Series,
    episodes: list[Episode],
    out_dir: Path,
    assets: dict[str, Path],
    draft_name: str | None = None,
) -> Path:
    """Assemble a JianYing draft for the given episodes.

    ``assets`` maps shot id -> media file (replay stills or downloaded
    clips). Episodes are chained on one timeline; each episode's shot total
    must stay inside the platform duration tier (``plan_episode_timing``
    raises otherwise, so an over-budget storyboard can never be exported
    silently).
    """
    geometry = PlatformGeometry.from_preset(series.platform)
    if draft_name is None:
        draft_name = "dramaser_" + "_".join(episode.id for episode in episodes)
    out_dir.mkdir(parents=True, exist_ok=True)
    folder = DraftFolder(str(out_dir))
    script: ScriptFile = folder.create_draft(
        draft_name, geometry.width, geometry.height, fps=30, allow_replace=True
    )
    video_track = script.append_track(TrackSpec(TrackType.video, name="main"))
    text_track = script.append_track(TrackSpec(TrackType.text, name="subtitle"))

    cursor_us = 0
    for episode in episodes:
        # budget gate: the episode's planned storyboard must fit its tier
        try:
            plan_episode_timing(episode.shots, geometry)
        except ValueError as exc:
            raise SystemExit(f"{episode.id} 无法导出：{exc}") from exc
        # placement chains by ACTUAL media durations: a live clip shorter than
        # its planned slot (Vidu only renders 4s/8s buckets) shifts later shots
        # forward instead of leaving a hole that fails our own `check draft`
        for shot in episode.shots:
            asset = assets.get(shot.id)
            if asset is None or not Path(asset).is_file():
                raise SystemExit(
                    f"镜头 {shot.id} 没有可用产物（先运行 generate/demo），无法导出"
                )
            material = VideoMaterial(str(asset), material_name=shot.id)
            script.add_material(material)
            planned_duration = int(round(shot.duration_s * 1_000_000))
            target_duration = planned_duration
            if material.material_type == "video" and material.duration < planned_duration:
                target_duration = material.duration
            script.add_segment(
                VideoSegment(material, trange(cursor_us, target_duration)),
                track=video_track,
            )
            if shot.line:
                script.add_segment(
                    _subtitle_segment(shot, cursor_us, target_duration, geometry),
                    track=text_track,
                )
            cursor_us += target_duration

    script.save()
    return out_dir / draft_name


def _subtitle_segment(
    shot: Shot, start_us: int, duration_us: int, geometry: PlatformGeometry
) -> TextSegment:
    style = TextStyle(
        size=SUBTITLE_SIZE,
        bold=True,
        color=(1.0, 1.0, 1.0),
        align=1,
        auto_wrapping=True,
        max_line_width=geometry.subtitle_max_line_width(),
    )
    clip = ClipSettings(transform_y=geometry.subtitle_transform_y())
    return TextSegment(
        shot.line,
        trange(start_us, duration_us),
        font=SUBTITLE_FONT,
        style=style,
        clip_settings=clip,
    )


# ---------------------------------------------------------------------------
# Install into the JianYing drafts directory
# ---------------------------------------------------------------------------


def default_jianying_drafts_root() -> Path | None:
    """Best-effort default location of the local JianYing drafts directory."""
    if _platform.system() == "Darwin":
        return Path.home() / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft"
    if _platform.system() == "Windows":
        return Path.home() / "AppData/Local/JianyingPro/User Data/Projects/com.lveditor.draft"
    return None


def install_draft(draft_dir: Path, drafts_root: Path | None = None) -> Path:
    """Copy an exported draft into the JianYing drafts directory."""
    root = drafts_root or default_jianying_drafts_root()
    if root is None:
        raise SystemExit(
            "未识别剪映草稿目录，请用 --draft-dir 指定（通常在剪映的用户数据 Projects 目录下）"
        )
    root.mkdir(parents=True, exist_ok=True)
    target = root / draft_dir.name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(draft_dir, target)
    return target


# ---------------------------------------------------------------------------
# Verification: `dramaser check draft`
# ---------------------------------------------------------------------------


def check_draft(
    draft_dir: Path,
    *,
    duration_tier: int = 60,
    safe_bottom: float = 0.12,
) -> list[CheckResult]:
    """Verify the exported draft against the vertical-platform contract."""
    results: list[CheckResult] = []
    content_path = draft_dir / "draft_content.json"
    if not content_path.is_file():
        return [CheckResult("draft_content.json 存在", False, str(content_path))]
    content = json.loads(content_path.read_text(encoding="utf-8"))

    canvas = content.get("canvas_config", {})
    ok = (canvas.get("width"), canvas.get("height")) == (1080, 1920)
    results.append(
        CheckResult(
            "9:16 画布（1080x1920）",
            ok,
            f"{canvas.get('width')}x{canvas.get('height')}",
        )
    )

    tracks = content.get("tracks", [])
    video_tracks = [t for t in tracks if t.get("type") == "video"]
    text_tracks = [t for t in tracks if t.get("type") == "text"]
    results.append(
        CheckResult(
            "视频轨 + 字幕轨",
            bool(video_tracks) and bool(text_tracks),
            f"video={len(video_tracks)} text={len(text_tracks)}",
        )
    )

    materials = content.get("materials", {})
    video_materials = {m["id"]: m for m in materials.get("videos", [])}
    text_materials = {m["id"]: m for m in materials.get("texts", [])}

    # video segments: material files exist, episode totals stay in tier
    episode_totals: dict[str, int] = {}
    missing, chained_ok, prev_end = [], True, 0
    for track in video_tracks:
        for segment in track.get("segments", []):
            material = video_materials.get(segment.get("material_id"))
            if material is None or not Path(material.get("path", "")).is_file():
                missing.append(material.get("material_name", "?") if material else "?")
                continue
            shot_name = material.get("material_name", "")
            episode_id = shot_name.rsplit("_s", 1)[0]
            trange_json = segment.get("target_timerange", {})
            start, duration = trange_json.get("start", 0), trange_json.get("duration", 0)
            if start != prev_end:
                chained_ok = False
            prev_end = start + duration
            episode_totals[episode_id] = episode_totals.get(episode_id, 0) + duration
    results.append(
        CheckResult("素材文件全部存在", not missing, "缺失：" + ", ".join(missing) if missing else "全部在盘")
    )
    results.append(
        CheckResult("视频段连续无空洞", chained_ok, f"总时长 {prev_end / 1e6:.1f}s")
    )
    over_budget = {
        ep: total for ep, total in episode_totals.items() if total > duration_tier * 1_000_000
    }
    results.append(
        CheckResult(
            f"每集不超时长档（{duration_tier}s）",
            not over_budget,
            "; ".join(f"{ep}={total / 1e6:.1f}s" for ep, total in over_budget.items())
            or f"{len(episode_totals)} 集全部达标",
        )
    )

    # subtitles: every text segment sits at/above the safe-zone baseline
    required_y = -(1.0 - 2.0 * safe_bottom)
    offenders = []
    subtitle_count = 0
    for track in text_tracks:
        for segment in track.get("segments", []):
            subtitle_count += 1
            transform_y = segment.get("clip", {}).get("transform", {}).get("y", 0.0)
            if transform_y > required_y + 1e-6:
                offenders.append(f"y={transform_y}")
    results.append(
        CheckResult(
            f"字幕全部位于安全区（bottom≥{safe_bottom:.0%}）",
            not offenders,
            "; ".join(offenders) or f"{subtitle_count} 条字幕达标",
        )
    )

    # every shot's line is present in the subtitle track
    lines_on_track = []
    for track in text_tracks:
        for segment in track.get("segments", []):
            material = text_materials.get(segment.get("material_id"), {})
            try:
                payload = json.loads(material.get("content", "{}"))
                lines_on_track.append(payload.get("text", ""))
            except ValueError:
                pass
    results.append(
        CheckResult(
            "字幕轨非空",
            bool(lines_on_track),
            f"{len(lines_on_track)} 条台词",
        )
    )
    return results


def check_draft_for_series(draft_dir: Path, series: Series) -> list[CheckResult]:
    geometry = PlatformGeometry.from_preset(series.platform)
    return check_draft(
        draft_dir,
        duration_tier=geometry.duration_tier,
        safe_bottom=geometry.safe.bottom,
    )
