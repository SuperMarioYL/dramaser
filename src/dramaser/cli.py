"""dramaser CLI: character-consistent AI production for vertical drama series.

Commands (see the README quickstart for the full journey):

- ``demo``      — no-key replay: drift-vs-locked GIF + report + JianYing draft
- ``init``      — scaffold an editable series project from a preset
- ``generate``  — live generation (Vidu 主体参考) + GLM-4V-Flash consistency gate
- ``regen``     — regenerate only drifted shots (shot lineage keeps the rest)
- ``export``    — export a JianYing-openable draft
- ``check``     — quality gates: ``check consistency`` / ``check draft``
"""

from __future__ import annotations

import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .consistency import (
    Glm4VFlashJudge,
    ReplayJudge,
    apply_gate,
    extract_judge_frame,
)
from .jianying import (
    check_draft,
    check_draft_for_series,
    export_draft,
    install_draft,
)
from .providers.base import GenerationRequest, ReplayProvider, make_provider
from .report import (
    GifStep,
    print_report_table,
    render_comparison_gif,
    render_report,
    write_demo_results,
)
from .series import (
    Series,
    load_preset,
    resolve_assets_root,
)
from .storyboard import Shot, ShotStatus

app = typer.Typer(
    help="给微短剧与竖屏 IP 账号的角色一致性 AI 产片工具，成片直出剪映。",
    no_args_is_help=True,
    add_completion=False,
)
check_app = typer.Typer(help="质量门：一致性评分与 draft 校验", no_args_is_help=True)
app.add_typer(check_app, name="check")

console = Console()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_series(series_arg: Path) -> tuple[Series, Path]:
    path = Path(series_arg)
    if not path.is_file():
        raise SystemExit(f"找不到系列工程文件：{path}（先运行 dramaser init）")
    return Series.load(path), path.parent


def _repo_relative(path: Path) -> str:
    path = Path(path).resolve()
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _file_sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel_asset(series_dir: Path, asset: Path) -> str:
    try:
        return str(Path(asset).resolve().relative_to(series_dir.resolve()))
    except ValueError:
        return str(Path(asset).resolve())


def _resolve_asset(series_dir: Path, ref: str) -> Path:
    path = Path(ref)
    return path if path.is_absolute() else series_dir / path


def _print_check_results(results) -> bool:
    table = Table(title="check draft")
    table.add_column("检查项")
    table.add_column("结果", justify="center")
    table.add_column("说明", overflow="fold")
    all_ok = True
    for result in results:
        all_ok = all_ok and result.ok
        table.add_row(result.name, "通过" if result.ok else "未过", result.detail)
    console.print(table)
    return all_ok


# ---------------------------------------------------------------------------
# demo — the no-key m1 milestone
# ---------------------------------------------------------------------------


@app.command()
def demo(
    install: bool = typer.Option(False, "--install-draft", help="把 draft 拷入剪映草稿目录"),
    out: Path = typer.Option(Path("dramaser-demo"), "--out", help="输出目录"),
    preset: str = typer.Option("ayao", "--preset", help="预置角色集"),
) -> None:
    """无 key 回放：3 集竖屏漂移-vs-锁定对比 GIF、一致性报告与剪映 draft。"""
    started = datetime.now(timezone.utc)
    series, _preset_dir = load_preset(preset)
    threshold = series.gates.consistency_threshold
    provider = ReplayProvider()

    # locked baseline: the character sheet's unperturbed render signature
    first_shot = series.all_shots()[0]
    baseline = provider.character_params(
        GenerationRequest(shot=first_shot, series=series, out_path=Path("/dev/null"))
    ).signature()
    judge = ReplayJudge(baseline)

    run_id = uuid.uuid4().hex[:12]
    drift_series = series.model_copy(deep=True)
    locked_series = series.model_copy(deep=True)
    gif_steps: list[GifStep] = []
    locked_assets: dict[str, Path] = {}

    with console.status("[bold]回放生成 9 个镜头（drift / locked）…") as status:
        for index, (shot, drift_shot, locked_shot) in enumerate(
            zip(series.all_shots(), drift_series.all_shots(), locked_series.all_shots())
        ):
            status.update(f"[bold]回放 {shot.id}（{index + 1}/9）")
            drift_path = out / "frames" / "drift" / f"{shot.id}.png"
            locked_path = out / "frames" / "locked" / f"{shot.id}.png"
            drift_result = provider.generate(
                GenerationRequest(shot, series, drift_path, mode="drift", run_id=run_id)
            )
            locked_result = provider.generate(
                GenerationRequest(shot, series, locked_path, mode="locked", run_id=run_id)
            )
            drift_verdict = judge.score_shot(drift_path, drift_shot, drift_series)
            locked_verdict = judge.score_shot(locked_path, locked_shot, locked_series)
            apply_gate(drift_shot, drift_verdict, threshold)
            apply_gate(locked_shot, locked_verdict, threshold)
            drift_shot.lineage.run_id = run_id
            locked_shot.lineage.run_id = run_id
            drift_shot.lineage.provider = drift_result.provider
            locked_shot.lineage.provider = locked_result.provider
            drift_shot.lineage.params_hash = drift_result.params_hash
            locked_shot.lineage.params_hash = locked_result.params_hash
            locked_assets[shot.id] = locked_path
            gif_steps.append(
                GifStep(
                    shot=shot,
                    drift=drift_path,
                    locked=locked_path,
                    drift_score=drift_shot.lineage.judge_score or 0.0,
                    locked_score=locked_shot.lineage.judge_score or 0.0,
                    threshold=threshold,
                )
            )

    gif_path = out / "demo-drift-vs-locked.gif"
    render_comparison_gif(gif_steps, gif_path, series.title)
    report_path = render_report(
        series,
        {s.id: s for s in drift_series.all_shots()},
        {s.id: s for s in locked_series.all_shots()},
        out / "consistency-report.md",
        threshold,
        mode="replay",
    )
    draft_dir = export_draft(
        locked_series,
        locked_series.episodes,
        out / "jianying",
        locked_assets,
        draft_name="dramaser_demo",
    )

    drift_failed = sum(
        1 for s in drift_series.all_shots() if s.lineage.status == ShotStatus.drifted
    )
    total = len(locked_assets)

    installed = None
    if install:
        installed = install_draft(draft_dir)

    console.print(print_report_table(
        series,
        {s.id: s for s in drift_series.all_shots()},
        {s.id: s for s in locked_series.all_shots()},
        threshold,
    ))

    command = "uv run dramaser demo"
    if out != Path("dramaser-demo"):
        command += f" --out {out}"
    if install:
        command += " --install-draft"
    output = "\n".join(
        [
            f"demo 完成（replay 模式，无 API key）",
            f"  对比 GIF   : {_repo_relative(gif_path)}",
            f"  一致性报告 : {_repo_relative(report_path)}",
            f"  剪映 draft : {_repo_relative(draft_dir / 'draft_content.json')}",
            f"  无锁定     : {drift_failed}/{total} 镜头漂移（门限 {threshold:.2f}）",
            f"  dramaser   : {total - sum(1 for s in locked_series.all_shots() if s.lineage.status == ShotStatus.drifted)}/{total} 镜头锁定",
        ]
        + (
            [f"  已拷入剪映 : {installed}"]
            if installed
            else ["  拷入剪映   : 重跑加 --install-draft"]
        )
    )
    console.print(output)

    assets_root = resolve_assets_root()
    write_demo_results(
        {
            "product": "dramaser",
            "version": __version__,
            "mode": "replay",
            "recorded_at": started.isoformat(timespec="seconds"),
            "scope": (
                "无 key 回放完整链路：预置角色集 → replay provider 渲染 3 集 x 3 分镜"
                "（drift / locked 双路）→ 回放一致性评分与门 → 漂移对比 GIF + 报告 + "
                "剪映 draft 导出。非真实模型输出；live 生成需 VIDU_API_KEY / ZHIPU_API_KEY。"
            ),
            "inputs": [
                {
                    "path": _repo_relative(assets_root / "presets" / preset / "series.yaml"),
                    "sha256": _file_sha256(assets_root / "presets" / preset / "series.yaml"),
                },
                {
                    "path": _repo_relative(assets_root / "demo-replay" / "replay.yaml"),
                    "sha256": _file_sha256(assets_root / "demo-replay" / "replay.yaml"),
                },
            ],
            "steps": [{"command": command, "output": output, "exit_code": 0}],
            "artifacts": {
                "gif": _repo_relative(gif_path),
                "report": _repo_relative(report_path),
                "draft": _repo_relative(draft_dir / "draft_content.json"),
            },
            "threshold": threshold,
            "scores": {
                "drift": {
                    s.id: s.lineage.judge_score for s in drift_series.all_shots()
                },
                "locked": {
                    s.id: s.lineage.judge_score for s in locked_series.all_shots()
                },
            },
            "notes": [
                "replay 模式：帧由预置角色参数确定性渲染（Pillow 示意渲染，非模型输出）。",
                "replay 评分为渲染参数签名与锁定基准的加权距离；live 模式由 GLM-4V-Flash 结构化打分，门接口一致。",
                "draft 画布 1080x1920（9:16），字幕落在安全区（bottom 12%），每集不超 60s 时长档。",
            ],
        },
        out / "demo-results.json",
    )


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    dir: Path = typer.Argument(Path("my-series"), help="目标目录"),
    preset: str = typer.Option("ayao", "--preset", help="预置角色集"),
) -> None:
    """从预置角色集生成可编辑的 series.yaml 系列工程。"""
    _series, preset_dir = load_preset(preset)
    target = Path(dir)
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"目标目录非空：{target}")
    shutil.copytree(preset_dir, target)
    console.print(f"[green]已创建系列工程[/green]：{target}")
    console.print(f"  编辑分镜：{target / 'series.yaml'}")
    console.print("  下一步   ：设置 VIDU_API_KEY / ZHIPU_API_KEY 后运行 "
                  f"[bold]dramaser generate ep01[/bold]")


# ---------------------------------------------------------------------------
# generate / regen — the m3 live loop
# ---------------------------------------------------------------------------


def _generate_shots(
    series: Series,
    series_dir: Path,
    shots: list[Shot],
    provider_name: str,
) -> None:
    provider = make_provider(provider_name)
    judge = ReplayJudge() if provider_name == "replay" else Glm4VFlashJudge()
    if provider_name == "replay":
        first = series.all_shots()[0]
        judge.set_baseline(
            ReplayProvider().character_params(
                GenerationRequest(shot=first, series=series, out_path=Path("/dev/null"))
            ).signature()
        )
    run_id = uuid.uuid4().hex[:12]
    for shot in shots:
        console.print(f"  生成 [bold]{shot.id}[/bold]（{shot.camera}）…")
        suffix = ".png" if provider_name == "replay" else ".mp4"
        asset_path = series_dir / "runs" / run_id / f"{shot.id}{suffix}"
        request = GenerationRequest(
            shot=shot,
            series=series,
            out_path=asset_path,
            series_dir=series_dir,
            run_id=run_id,
        )
        result = provider.generate(request)
        judge_frame = extract_judge_frame(result.asset, asset_path.parent)
        verdict = judge.score_shot(judge_frame, shot, series)
        apply_gate(shot, verdict, series.gates.consistency_threshold)
        shot.lineage.run_id = run_id
        shot.lineage.provider = result.provider
        shot.lineage.params_hash = result.params_hash
        shot.lineage.asset = _rel_asset(series_dir, result.asset)
        mark = "锁定" if shot.lineage.status == ShotStatus.locked else "漂移"
        console.print(
            f"    一致性 {shot.lineage.judge_score:.2f} → {mark}"
            f"（门限 {series.gates.consistency_threshold:.2f}）"
        )


@app.command()
def generate(
    episode: str = typer.Argument(..., help="分集 id，如 ep01"),
    series_arg: Path = typer.Option(Path("series.yaml"), "--series"),
    provider: str = typer.Option("vidu", "--provider", help="vidu | replay"),
) -> None:
    """live 生成分集（Vidu 主体参考），逐镜头过 GLM-4V-Flash 一致性门。"""
    series, series_dir = _load_series(series_arg)
    try:
        episode_obj = series.episode(episode)
    except KeyError:
        raise SystemExit(f"分集 {episode} 不存在；可用：{[e.id for e in series.episodes]}")
    console.print(f"[bold]{series.title} · {episode} {episode_obj.title}[/bold]")
    _generate_shots(series, series_dir, list(episode_obj.shots), provider)
    series.save(series_dir / "series.yaml")
    report_path = render_report(
        series,
        {},
        {s.id: s for s in episode_obj.shots},
        series_dir / "reports" / f"consistency-{episode}.md",
        series.gates.consistency_threshold,
        mode=f"live/{provider}",
    )
    drifted = [s.id for s in episode_obj.shots if s.lineage.status == ShotStatus.drifted]
    console.print(f"[green]血缘已写入[/green] {series_dir / 'series.yaml'}")
    console.print(f"[green]报告[/green] {_repo_relative(report_path)}")
    if drifted:
        console.print(f"[yellow]漂移镜头[/yellow]：{', '.join(drifted)} → 运行 "
                      f"[bold]dramaser regen {drifted[0]}[/bold]")


@app.command()
def regen(
    shot_id: str = typer.Argument(
        None, help="镜头 id（如 ep01_s03）；缺省时重生成全部漂移镜头"
    ),
    series_arg: Path = typer.Option(Path("series.yaml"), "--series"),
    provider: str = typer.Option("vidu", "--provider", help="vidu | replay"),
) -> None:
    """分镜级局部重生成：只重烧目标镜头，其余血缘与产物保持不变。"""
    series, series_dir = _load_series(series_arg)
    if shot_id is None:
        targets = [s for s in series.all_shots() if s.lineage.status == ShotStatus.drifted]
        if not targets:
            console.print("没有状态为 drifted 的镜头，无需 regen。")
            return
    else:
        try:
            targets = [series.shot(shot_id)]
        except KeyError:
            raise SystemExit(f"镜头 {shot_id} 不存在")
    before = {s.id: (s.lineage.params_hash, s.lineage.asset) for s in series.all_shots()}
    console.print(f"regen {len(targets)} 个镜头：{[s.id for s in targets]}")
    _generate_shots(series, series_dir, targets, provider)
    series.save(series_dir / "series.yaml")
    unchanged = [
        s.id
        for s in series.all_shots()
        if s.id not in {t.id for t in targets}
        and before.get(s.id) == (s.lineage.params_hash, s.lineage.asset)
    ]
    console.print(
        f"[green]{len(unchanged)}[/green] 个镜头血缘未动；"
        f"重新导出运行 [bold]dramaser export {targets[0].episode_id}[/bold]"
    )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@app.command()
def export(
    episode: str = typer.Argument(..., help="分集 id，如 ep01"),
    series_arg: Path = typer.Option(Path("series.yaml"), "--series"),
    out: Path = typer.Option(Path("jianying-drafts"), "--out"),
    install: bool = typer.Option(False, "--install-draft", help="拷入剪映草稿目录"),
) -> None:
    """导出剪映可直接打开的 draft（9:16 画布、字幕安全区、时长档分段）。"""
    series, series_dir = _load_series(series_arg)
    try:
        episode_obj = series.episode(episode)
    except KeyError:
        raise SystemExit(f"分集 {episode} 不存在；可用：{[e.id for e in series.episodes]}")
    assets = {
        shot.id: _resolve_asset(series_dir, shot.lineage.asset)
        for shot in episode_obj.shots
        if shot.lineage.asset
    }
    draft_dir = export_draft(series, [episode_obj], out, assets)
    results = check_draft_for_series(draft_dir, series)
    all_ok = _print_check_results(results)
    console.print(f"[green]draft[/green]：{draft_dir / 'draft_content.json'}")
    if install:
        installed = install_draft(draft_dir)
        console.print(f"[green]已拷入剪映草稿目录[/green]：{installed}")
    if not all_ok:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# check gates
# ---------------------------------------------------------------------------


@check_app.command("consistency")
def check_consistency(
    series_arg: Path = typer.Option(Path("series.yaml"), "--series"),
    judge_name: str = typer.Option(
        "auto", "--judge", help="auto | glm | replay（auto 按血缘 provider 选择）"
    ),
) -> None:
    """复评全部已有镜头产物，更新锁定/漂移状态。"""
    series, series_dir = _load_series(series_arg)
    shots = [s for s in series.all_shots() if s.lineage.asset]
    if not shots:
        raise SystemExit("还没有任何镜头产物；先运行 generate 或 demo。")
    provider_name = judge_name
    if provider_name == "auto":
        provider_name = "replay" if shots[0].lineage.provider == "replay" else "glm"
    judge = ReplayJudge() if provider_name == "replay" else Glm4VFlashJudge()
    if provider_name == "replay":
        first = series.all_shots()[0]
        judge.set_baseline(
            ReplayProvider().character_params(
                GenerationRequest(shot=first, series=series, out_path=Path("/dev/null"))
            ).signature()
        )
    threshold = series.gates.consistency_threshold
    for shot in shots:
        asset = _resolve_asset(series_dir, shot.lineage.asset)
        if not asset.is_file():
            raise SystemExit(f"镜头 {shot.id} 的产物不存在：{asset}")
        verdict = judge.score_shot(asset, shot, series)
        apply_gate(shot, verdict, threshold)
        console.print(f"  {shot.id}: {shot.lineage.judge_score:.2f} "
                      f"{'锁定' if shot.lineage.status == ShotStatus.locked else '漂移'}")
    series.save(series_dir / "series.yaml")
    drifted = [s.id for s in shots if s.lineage.status == ShotStatus.drifted]
    console.print(f"[green]已更新[/green] {series_arg}；漂移镜头："
                  f"{', '.join(drifted) if drifted else '无'}")


@check_app.command("draft")
def check_draft_cmd(
    draft_dir: Path = typer.Argument(
        None, help="draft 目录（缺省取 jianying-drafts/ 下最新的 dramaser_*）"
    ),
    series_arg: Path = typer.Option(Path("series.yaml"), "--series"),
) -> None:
    """校验 draft 结构与平台规范（可打开性、安全区、时长档）。"""
    path = Path(draft_dir) if draft_dir else None
    if path is None:
        root = Path("jianying-drafts")
        candidates = sorted(root.glob("dramaser_*")) if root.is_dir() else []
        if not candidates:
            raise SystemExit("未找到 draft；请先运行 export 或指定目录")
        path = candidates[-1]
    if not (path / "draft_content.json").is_file():
        raise SystemExit(f"{path} 下没有 draft_content.json")
    series: Series | None = None
    if Path(series_arg).is_file():
        series = Series.load(series_arg)
    if series is not None:
        results = check_draft_for_series(path, series)
    else:
        results = check_draft(path)
    if not _print_check_results(results):
        raise SystemExit(1)


if __name__ == "__main__":  # pragma: no cover
    app()
