"""The demo must not report success when its locked lane fails the gate.

``dramaser demo`` is the no-key headline proof; a regression in the replay
render/judge path used to print "dramaser : 0/9 镜头锁定" and still exit 0.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))

from dramaser.cli import app  # noqa: E402


def test_demo_fails_when_locked_lane_does_not_pass_gate(
    tmp_path: Path, monkeypatch
) -> None:
    assets = tmp_path / "assets"
    shutil.copytree(REPO_ROOT / "assets", assets)
    preset = assets / "presets" / "ayao" / "series.yaml"
    preset.write_text(
        preset.read_text(encoding="utf-8").replace(
            "consistency_threshold: 0.85", "consistency_threshold: 1.01"
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DRAMASER_ASSETS", str(assets))

    result = CliRunner().invoke(app, ["demo", "--out", str(tmp_path / "demo")])

    assert result.exit_code == 1, result.output
    assert "未过一致性门" in result.output
