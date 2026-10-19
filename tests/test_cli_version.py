"""CLI surface test: --version reports the release version (dramaser-quality-1)."""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))

from dramaser.cli import app  # noqa: E402


def test_cli_reports_version() -> None:
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert "dramaser 0.2.0" in result.output
