"""Consistency report rendering: the drift-vs-locked comparison GIF, the
per-shot scoring report (Markdown + rich terminal), and the recorded
``docs/demo-results.json`` writer.

The GIF is the demo's money shot: for each shot of each episode it shows the
un-locked render next to the dramaser-locked render with both consistency
scores, so cross-episode drift is visible at a glance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from rich.console import Console
from rich.table import Table

from .series import Series
from .storyboard import Shot, ShotStatus

# -- layout constants ---------------------------------------------------------

PANEL_W, PANEL_H = 288, 512  # exact 9:16
MARGIN = 24
GIF_W = MARGIN + PANEL_W + 16 + PANEL_W + MARGIN
HEADER_H = 96
PANEL_HEADER_H = 34
FOOTER_H = 66
GIF_H = MARGIN + HEADER_H + PANEL_HEADER_H + PANEL_H + 40 + FOOTER_H + MARGIN
FRAME_MS = 1200

_BG = (18, 16, 26)
_PANEL_BG = (26, 23, 38)
_INK = (240, 238, 246)
_MUTED = (154, 148, 176)
_DRIFT = (235, 162, 96)
_LOCK = (122, 214, 168)
_BADGE_DRIFT = (44, 30, 30)
_BADGE_LOCK = (22, 42, 36)

_CJK_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


@dataclass
class GifStep:
    """One GIF frame: the same shot rendered un-locked vs locked."""

    shot: Shot
    drift: Path
    locked: Path
    drift_score: float
    locked_score: float
    threshold: float


def load_cjk_font(size: int) -> ImageFont.FreeTypeFont | None:
    for candidate in _CJK_FONT_CANDIDATES:
        if Path(candidate).is_file():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return None


def _text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill) -> None:
    if font is None:
        draw.text(xy, text.encode("ascii", "replace").decode("ascii"), fill=fill)
    else:
        draw.text(xy, text, font=font, fill=fill)


def _score_chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    score: float,
    threshold: float,
    label: str,
    font,
) -> None:
    ok = score >= threshold
    color = _LOCK if ok else _DRIFT
    badge = _BADGE_LOCK if ok else _BADGE_DRIFT
    draw.rounded_rectangle([x, y, x + width, y + 34], radius=8, fill=badge)
    status = "锁定" if ok else "漂移"
    _text(draw, (x + 10, y + 7), f"{label} {score:.2f} {status}", font, color)


def render_comparison_gif(steps: list[GifStep], out_path: Path, title: str) -> None:
    """Render the animated drift-vs-locked comparison GIF."""
    font_title = load_cjk_font(26)
    font_sub = load_cjk_font(15)
    font_chip = load_cjk_font(15)
    font_small = load_cjk_font(13)
    frames: list[Image.Image] = []
    left_x = MARGIN
    right_x = MARGIN + PANEL_W + 16
    panel_top = MARGIN + HEADER_H + PANEL_HEADER_H

    for step in steps:
        frame = Image.new("RGB", (GIF_W, GIF_H), _BG)
        draw = ImageDraw.Draw(frame)
        _text(draw, (MARGIN, MARGIN), title, font_title, _INK)
        _text(
            draw,
            (MARGIN, MARGIN + 40),
            "左：无主体参考（漂移）  ·  右：dramaser 锁定",
            font_sub,
            _MUTED,
        )
        _text(draw, (left_x, MARGIN + HEADER_H), "无主体参考", font_sub, _DRIFT)
        _text(draw, (right_x, MARGIN + HEADER_H), "dramaser 锁定", font_sub, _LOCK)

        for x, image_path in ((left_x, step.drift), (right_x, step.locked)):
            panel = Image.open(image_path).convert("RGB").resize((PANEL_W, PANEL_H))
            frame.paste(panel, (x, panel_top))
            # subtitle safe zone (bottom 12%) with the shot's line
            zone_top = panel_top + int(PANEL_H * (1 - 0.12)) - 8
            shade = Image.new("RGBA", (PANEL_W, PANEL_H - (zone_top - panel_top)), (0, 0, 0, 110))
            frame.paste(shade, (x, zone_top), shade)
            _text(draw, (x + 12, zone_top + 10), step.shot.line[:14], font_small, _INK)

        _score_chip(
            draw, left_x, panel_top + PANEL_H + 10, PANEL_W,
            step.drift_score, step.threshold, "一致性", font_chip,
        )
        _score_chip(
            draw, right_x, panel_top + PANEL_H + 10, PANEL_W,
            step.locked_score, step.threshold, "一致性", font_chip,
        )
        footer_y = GIF_H - FOOTER_H - MARGIN + 8
        _text(
            draw,
            (MARGIN, footer_y),
            f"{step.shot.episode_id} · {step.shot.camera} · {step.shot.id}",
            font_sub,
            _INK,
        )
        _text(
            draw,
            (MARGIN, footer_y + 24),
            f"门限 {step.threshold:.2f} · replay 演示，记录见 demo-results.json",
            font_small,
            _MUTED,
        )
        frames.append(frame)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
    )


# ---------------------------------------------------------------------------
# Consistency report
# ---------------------------------------------------------------------------


def _status_mark(shot: Shot) -> str:
    if shot.lineage.status == ShotStatus.drifted:
        return "漂移"
    if shot.lineage.status == ShotStatus.locked:
        return "锁定"
    return "未生成"


def render_report(
    series: Series,
    drift_shots: dict[str, Shot],
    locked_shots: dict[str, Shot],
    out_path: Path,
    threshold: float,
    mode: str,
) -> Path:
    """Write the Markdown consistency report."""
    lines = [
        f"# {series.title} — 一致性报告",
        "",
        f"- 模式：{mode}",
        f"- 一致性门限：{threshold:.2f}",
        f"- 生成时间：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "## 逐镜头评分",
        "",
        "| 镜头 | 台词 | 无锁定评分 | 状态 | 锁定评分 | 状态 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for episode in series.episodes:
        lines.append(f"| **{episode.id} {episode.title}** | | | | | |")
        for shot in episode.shots:
            drift = drift_shots.get(shot.id)
            locked = locked_shots.get(shot.id)
            drift_score = f"{drift.lineage.judge_score:.2f}" if drift and drift.lineage.judge_score is not None else "-"
            locked_score = f"{locked.lineage.judge_score:.2f}" if locked and locked.lineage.judge_score is not None else "-"
            lines.append(
                f"| {shot.id} | {shot.line} | {drift_score} | "
                f"{_status_mark(drift) if drift else '-'} | {locked_score} | "
                f"{_status_mark(locked) if locked else '-'} |"
            )
    drift_failed = sum(
        1
        for shot in drift_shots.values()
        if shot.lineage.status == ShotStatus.drifted
    )
    locked_failed = sum(
        1
        for shot in locked_shots.values()
        if shot.lineage.status == ShotStatus.drifted
    )
    total = len(locked_shots) or 1
    lines += [
        "",
        "## 结论",
        "",
        f"- 无主体参考：{drift_failed}/{total} 镜头未过一致性门，跨集漂移。",
        f"- dramaser 锁定：{total - locked_failed}/{total} 镜头通过一致性门。",
        "",
        "> replay 模式评分为渲染参数签名与锁定基准的加权距离（无 key、确定性）；"
        "live 模式由 GLM-4V-Flash 逐镜头结构化打分，门接口一致。",
        "",
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def print_report_table(
    series: Series,
    drift_shots: dict[str, Shot],
    locked_shots: dict[str, Shot],
    threshold: float,
) -> Table:
    """Rich terminal table mirroring the Markdown report."""
    table = Table(title=f"{series.title} · 逐镜头一致性（门限 {threshold:.2f}）")
    table.add_column("镜头", style="dim")
    table.add_column("台词", overflow="fold")
    table.add_column("无锁定", justify="right")
    table.add_column("锁定", justify="right")
    for episode in series.episodes:
        for shot in episode.shots:
            drift = drift_shots.get(shot.id)
            locked = locked_shots.get(shot.id)

            def fmt(target: Shot | None) -> str:
                if target is None or target.lineage.judge_score is None:
                    return "-"
                score = f"{target.lineage.judge_score:.2f}"
                if target.lineage.status == ShotStatus.drifted:
                    return f"{score} 漂移"
                return f"{score} 锁定"

            table.add_row(shot.id, shot.line, fmt(drift), fmt(locked))
    return table


# ---------------------------------------------------------------------------
# Demo results recorder
# ---------------------------------------------------------------------------


def write_demo_results(data: dict, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out_path
