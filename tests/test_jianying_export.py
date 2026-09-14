"""Tests for the JianYing draft export path (m2) and the contracts it relies on.

Covers: SeriesAsset YAML round-trip, repo/bundled preset sync, draft structure
(9:16 canvas, chained per-shot segments, subtitle safe zone, duration tier),
``check draft`` verification including tamper detection, and replay determinism.
"""

from __future__ import annotations

import filecmp
import json
import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))

from dramaser.jianying import check_draft, export_draft, install_draft  # noqa: E402
from dramaser.providers.base import GenerationRequest, ReplayProvider  # noqa: E402
from dramaser.series import (  # noqa: E402
    Series,
    load_preset,
    resolve_assets_root,
    yaml_dump,
    yaml_load,
)
from dramaser.storyboard import (  # noqa: E402
    PlatformGeometry,
    SubtitleSafeZone,
    PlatformPreset,
    Shot,
    plan_episode_timing,
)


def _make_png(path: Path, color: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (720, 1280), color).save(path)
    return path


def _make_series(tmp_path: Path) -> tuple[Series, dict[str, Path]]:
    """Two episodes x two shots, stills on disk, preset platform rules."""
    platform = PlatformPreset(
        preset="douyin_60s",
        aspect="9:16",
        duration_tier=60,
        subtitle_safe_zone=SubtitleSafeZone(top=0.08, bottom=0.12, right_rail=0.10),
    )
    assets: dict[str, Path] = {}
    episodes = []
    palette = iter([(40, 44, 70), (70, 60, 50), (50, 60, 55), (60, 50, 60)])
    for episode_id in ("ep01", "ep02"):
        shots = []
        for index in (1, 2):
            shot_id = f"{episode_id}_s0{index}"
            assets[shot_id] = _make_png(
                tmp_path / "assets" / f"{shot_id}.png", next(palette)
            )
            shots.append(
                Shot(
                    id=shot_id,
                    characters=["ayao"],
                    line=f"台词 {shot_id}",
                    action="测试动作",
                    camera="中景",
                    duration_s=5.0,
                )
            )
        episodes.append({"id": episode_id, "title": "测试", "shots": [s.model_dump() for s in shots]})
    series = Series(
        title="测试系列",
        genre="单元测试",
        characters=[
            {
                "id": "ayao",
                "name": "阿瑶",
                "appearance_lock": {"脸型": "鹅蛋脸"},
                "reference_images": ["reference/ayao.png"],
            }
        ],
        platform=platform.model_dump(),
        episodes=episodes,
    )
    return series, assets


# ---------------------------------------------------------------------------
# SeriesAsset YAML round-trip + preset sync
# ---------------------------------------------------------------------------


def test_yaml_subset_roundtrip() -> None:
    tree = {
        "title": "咖啡与晚风",
        "aspect": "9:16",
        "plain_colon": "a: b 值",
        "hash_item": "#2b2340",
        "numbers": [1, 2.5, True, None],
        "nested": {"k": ["v1", "v2"]},
        "cjk": "三年了，这家店的灯还是没换过。",
    }
    assert yaml_load(yaml_dump(tree)) == tree


def test_preset_series_roundtrip() -> None:
    series, _ = load_preset("ayao")
    text = yaml_dump(series.to_doc())
    assert Series.model_validate(yaml_load(text)).model_dump() == series.model_dump()


def test_bundled_assets_in_sync_with_repo() -> None:
    """The wheel-bundled copies must stay byte-identical to assets/ (uvx demo)."""
    repo_assets = REPO_ROOT / "assets"
    bundled = REPO_ROOT / "src" / "dramaser" / "bundled"
    for relative in ("presets/ayao/series.yaml", "presets/ayao/reference/ayao.png",
                     "demo-replay/replay.yaml"):
        assert (repo_assets / relative).is_file(), f"missing repo asset {relative}"
        assert (bundled / relative).is_file(), f"missing bundled asset {relative}"
        assert filecmp.cmp(repo_assets / relative, bundled / relative, shallow=False), (
            f"{relative} 与打包副本不一致：请同步 src/dramaser/bundled/"
        )


# ---------------------------------------------------------------------------
# Platform geometry rules
# ---------------------------------------------------------------------------


def test_platform_geometry_safe_zone() -> None:
    geometry = PlatformGeometry.from_preset(
        PlatformPreset(subtitle_safe_zone=SubtitleSafeZone(bottom=0.12, right_rail=0.10))
    )
    assert (geometry.width, geometry.height) == (1080, 1920)
    # subtitle center sits one bottom-fraction above the canvas bottom edge
    assert geometry.subtitle_transform_y() == pytest.approx(-0.76)
    assert geometry.subtitle_max_line_width() == pytest.approx(0.80)


def test_duration_tier_enforced() -> None:
    geometry = PlatformGeometry.from_preset(PlatformPreset(duration_tier=60))
    shots = [Shot(id=f"ep01_s{i:02d}", duration_s=5.0) for i in range(13)]  # 65s
    with pytest.raises(ValueError):
        plan_episode_timing(shots, geometry)


# ---------------------------------------------------------------------------
# Draft export + check
# ---------------------------------------------------------------------------


def _export(tmp_path: Path) -> Path:
    series, assets = _make_series(tmp_path)
    return export_draft(series, series.episodes, tmp_path / "jianying", assets)


def test_export_draft_structure(tmp_path: Path) -> None:
    draft_dir = _export(tmp_path)
    content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    assert (content["canvas_config"]["width"], content["canvas_config"]["height"]) == (1080, 1920)
    tracks = {t["type"]: t for t in content["tracks"]}
    assert len(tracks["video"]["segments"]) == 4
    assert len(tracks["text"]["segments"]) == 4
    # video segments are chained without gaps
    starts = [s["target_timerange"]["start"] for s in tracks["video"]["segments"]]
    assert starts == [0, 5_000_000, 10_000_000, 15_000_000]
    assert content["duration"] == 20_000_000
    # every material is a shot-named file that exists on disk
    for material in content["materials"]["videos"]:
        assert Path(material["path"]).is_file()
        assert material["material_name"].startswith("ep0")


def test_subtitles_within_safe_zone(tmp_path: Path) -> None:
    draft_dir = _export(tmp_path)
    content = json.loads((draft_dir / "draft_content.json").read_text(encoding="utf-8"))
    text_track = next(t for t in content["tracks"] if t["type"] == "text")
    required_y = -(1.0 - 2.0 * 0.12)
    for segment in text_track["segments"]:
        assert segment["clip"]["transform"]["y"] <= required_y + 1e-9
    # the subtitle text material carries the shot line
    texts = content["materials"]["texts"]
    assert len(texts) == 4


def test_check_draft_passes_and_catches_tampering(tmp_path: Path) -> None:
    draft_dir = _export(tmp_path)
    results = check_draft(draft_dir, duration_tier=60, safe_bottom=0.12)
    by_name = {r.name: r for r in results}
    assert all(r.ok for r in results), [r.detail for r in results if not r.ok]
    assert by_name["9:16 画布（1080x1920）"].ok
    assert "3" not in by_name["每集不超时长档（60s）"].detail  # 2 episodes listed

    # tamper: move the first subtitle out of the safe zone
    content_path = draft_dir / "draft_content.json"
    content = json.loads(content_path.read_text(encoding="utf-8"))
    text_track = next(t for t in content["tracks"] if t["type"] == "text")
    text_track["segments"][0]["clip"]["transform"]["y"] = -0.3
    content_path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    results = check_draft(draft_dir, duration_tier=60, safe_bottom=0.12)
    safe_zone = next(r for r in results if "安全区" in r.name)
    assert not safe_zone.ok


def test_install_draft_copies_into_root(tmp_path: Path) -> None:
    draft_dir = _export(tmp_path)
    root = tmp_path / "jianying-root"
    installed = install_draft(draft_dir, drafts_root=root)
    assert (installed / "draft_content.json").is_file()
    assert installed.name == draft_dir.name


# ---------------------------------------------------------------------------
# Replay determinism (the no-key demo contract)
# ---------------------------------------------------------------------------


def test_replay_provider_is_deterministic(tmp_path: Path) -> None:
    series, _preset_dir = load_preset("ayao")
    provider = ReplayProvider(resolve_assets_root() / "demo-replay")
    shot = series.shot("ep02_s01")
    hashes = []
    for run in (1, 2):
        result = provider.generate(
            GenerationRequest(
                shot=shot,
                series=series,
                out_path=tmp_path / f"run{run}" / "ep02_s01.png",
                mode="drift",
                run_id=f"run{run}",
            )
        )
        hashes.append(result.params_hash)
    assert hashes[0] == hashes[1]
    # drift mode must actually perturb the signature away from the lock
    locked = provider.character_params(
        GenerationRequest(shot=shot, series=series, out_path=tmp_path / "x.png")
    )
    drifted = provider.character_params(
        GenerationRequest(shot=shot, series=series, out_path=tmp_path / "y.png", mode="drift")
    )
    assert drifted.signature() != locked.signature()
