"""CLI path contracts: write lineage back to the --series file, and let init
accept an already-created empty target directory (dramaser v0.3.0 fixes).

Before the fix, ``generate --series my_series.yaml`` saved lineage into a fresh
``series.yaml`` next to it, and ``init`` on an empty directory crashed with
FileExistsError.
"""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))

from dramaser.cli import app  # noqa: E402
from dramaser.series import Series  # noqa: E402


def test_generate_saves_lineage_to_series_option_path(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("DRAMASER_ASSETS", str(REPO_ROOT / "assets"))
    project = tmp_path / "project"
    result = CliRunner().invoke(app, ["init", "--preset", "ayao", str(project)])
    assert result.exit_code == 0, result.output

    series_path = project / "my_series.yaml"
    (project / "series.yaml").rename(series_path)
    result = CliRunner().invoke(
        app,
        ["generate", "ep01", "--provider", "replay", "--series", str(series_path)],
    )
    assert result.exit_code == 0, result.output
    assert result.exception is None

    # the loaded file is the one that receives lineage; no second series.yaml
    assert not (project / "series.yaml").exists()
    updated = Series.load(series_path)
    shot = updated.shot("ep01_s01")
    assert shot.lineage.run_id
    assert shot.lineage.status == "locked"
    assert shot.lineage.asset


def test_init_accepts_existing_empty_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DRAMASER_ASSETS", str(REPO_ROOT / "assets"))
    target = tmp_path / "my-series"
    target.mkdir()

    result = CliRunner().invoke(app, ["init", "--preset", "ayao", str(target)])

    assert result.exit_code == 0, result.output
    assert result.exception is None
    assert (target / "series.yaml").is_file()
    assert (target / "reference" / "ayao.png").is_file()
