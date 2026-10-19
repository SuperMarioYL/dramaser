"""Tests for the consistency-gate paths that need no API key.

Covers: ``check consistency`` re-judging video assets through an extracted
frame (parity with the generate path, dramaser-fix-1) and the live judge's
reference-image guard for shots without characters.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))

from dramaser.cli import app  # noqa: E402
from dramaser.consistency import Glm4VFlashJudge, ShotVerdict  # noqa: E402
from dramaser.series import Series, yaml_dump, yaml_load  # noqa: E402
from dramaser.storyboard import Shot, ShotStatus  # noqa: E402


def _make_mp4(path: Path, seconds: float) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not available")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c=blue:s=720x1280:d={seconds}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def _series_with_video_asset(tmp_path: Path) -> Path:
    """A series project whose ep01_s01 lineage asset is a Vidu-style mp4."""
    asset = _make_mp4(tmp_path / "assets" / "ep01_s01.mp4", 4)
    shot = Shot(
        id="ep01_s01",
        characters=["ayao"],
        line="第一句台词",
        duration_s=5.0,
    )
    shot.lineage.run_id = "runtest"
    shot.lineage.provider = "vidu"
    shot.lineage.params_hash = "deadbeef0000"
    shot.lineage.status = ShotStatus.drifted
    shot.lineage.asset = str(asset.relative_to(tmp_path))
    series = Series(
        title="复评测试",
        characters=[{"id": "ayao", "name": "阿瑶"}],
        episodes=[{"id": "ep01", "shots": [shot.model_dump()]}],
    )
    path = tmp_path / "series.yaml"
    path.write_text(yaml_dump(series.to_doc()), encoding="utf-8")
    return path


def test_check_consistency_judges_video_through_extracted_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0.1.0 passed the raw .mp4 to the judge; the gate must extract a frame."""
    series_path = _series_with_video_asset(tmp_path)
    mp4 = tmp_path / "assets" / "ep01_s01.mp4"

    received: list[Path] = []

    class StubJudge:
        name = "stub"

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def score_shot(self, frame: Path, shot: Shot, series: Series) -> ShotVerdict:
            received.append(Path(frame))
            return ShotVerdict(shot_id=shot.id, score=0.92, judge="stub")

    monkeypatch.setattr("dramaser.cli.Glm4VFlashJudge", StubJudge)

    result = CliRunner().invoke(
        app, ["check", "consistency", "--series", str(series_path), "--judge", "glm"]
    )
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert len(received) == 1
    frame = received[0]
    assert frame.suffix == ".png", f"judge received raw video asset: {frame}"
    assert frame.is_file()
    assert frame != mp4
    # the extracted frame lives next to the asset and the score was persisted
    assert frame.parent == mp4.parent
    updated = Series.load(series_path)
    assert updated.shot("ep01_s01").lineage.status == "locked"


def test_reference_image_guard_for_characterless_shot() -> None:
    """A shot with no characters must fail with a clean message, not IndexError."""
    series = Series(
        title="守护测试",
        characters=[{"id": "ayao", "name": "阿瑶", "reference_images": []}],
        episodes=[{"id": "ep01", "shots": [{"id": "ep01_s01", "characters": []}]}],
    )
    judge = Glm4VFlashJudge(api_key="test-key")
    shot = series.shot("ep01_s01")
    with pytest.raises(SystemExit, match="未绑定角色"):
        judge._reference_image(series, shot)


def test_empty_mapping_and_sequence_roundtrip() -> None:
    """The writer emits {} / [] for empty containers; the reader must read them back."""
    tree = {"a": {}, "b": [], "nested": {"c": {}, "d": []}}
    assert yaml_load(yaml_dump(tree)) == tree


def test_series_with_empty_lock_survives_save_load(tmp_path: Path) -> None:
    """generate→save must leave the series loadable when a character has no locks."""
    series = Series(
        title="空锁测试",
        characters=[{"id": "ayao", "name": "阿瑶"}],
        episodes=[{"id": "ep01", "shots": [{"id": "ep01_s01", "characters": ["ayao"]}]}],
    )
    path = tmp_path / "series.yaml"
    path.write_text(yaml_dump(series.to_doc()), encoding="utf-8")
    reloaded = Series.load(path)
    assert reloaded.model_dump() == series.model_dump()
