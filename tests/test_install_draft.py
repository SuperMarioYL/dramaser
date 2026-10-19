"""Tests for --install-draft --draft-dir (dramaser-fix-3).

The install target must be user-controllable: install_draft's error message
sends users to --draft-dir, which v0.1.0 never actually accepted.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))

from dramaser.cli import app  # noqa: E402
from dramaser.series import Series, yaml_dump  # noqa: E402
from dramaser.storyboard import Shot, ShotStatus  # noqa: E402


def test_demo_install_draft_into_custom_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DRAMASER_ASSETS", str(REPO_ROOT / "assets"))
    out = tmp_path / "demo-out"
    drafts_root = tmp_path / "drafts"
    result = CliRunner().invoke(
        app,
        ["demo", "--out", str(out), "--install-draft", "--draft-dir", str(drafts_root)],
    )
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert (drafts_root / "dramaser_demo" / "draft_content.json").is_file()


def test_export_install_draft_into_custom_dir(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "assets").mkdir(parents=True)
    shots = []
    for index in (1, 2):
        shot_id = f"ep01_s0{index}"
        Image.new("RGB", (720, 1280), (30 + index, 40, 60)).save(
            project / "assets" / f"{shot_id}.png"
        )
        shot = Shot(id=shot_id, characters=["ayao"], line=f"台词 {shot_id}")
        shot.lineage.run_id = "runtest"
        shot.lineage.provider = "replay"
        shot.lineage.params_hash = "abc123def456"
        shot.lineage.status = ShotStatus.locked
        shot.lineage.asset = f"assets/{shot_id}.png"
        shots.append(shot.model_dump())
    series = Series(
        title="导出测试",
        characters=[{"id": "ayao", "name": "阿瑶", "reference_images": []}],
        episodes=[{"id": "ep01", "shots": shots}],
    )
    (project / "series.yaml").write_text(yaml_dump(series.to_doc()), encoding="utf-8")

    drafts_root = tmp_path / "drafts"
    result = CliRunner().invoke(
        app,
        [
            "export", "ep01",
            "--series", str(project / "series.yaml"),
            "--out", str(tmp_path / "jianying"),
            "--install-draft", "--draft-dir", str(drafts_root),
        ],
    )
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert (drafts_root / "dramaser_ep01" / "draft_content.json").is_file()
