"""Character-sheet locking and the consistency gate.

Two responsibilities:

1. **角色设定集锁定** — turn a character's ``appearance_lock`` plus the series
   style locks into the prompt contract handed to the generation provider, so
   every shot of every episode describes the same person the same way.
2. **一致性门** — per-shot structured scoring against the lock. The live judge
   is GLM-4V-Flash through Zhipu's OpenAI-compatible endpoint (needs
   ``ZHIPU_API_KEY``); the replay judge scores the render signature stamped
   into demo artifacts, which keeps the no-key demo path deterministic.
   Both return the same :class:`ShotVerdict`, so the gate itself never
   changes when the judge does (v0.2 can swap in a local embedding judge).

GLM-4V-Flash is a vision model: ``response_format: json_object`` is only
supported on text models, so the JSON shape is agreed in the prompt and parsed
defensively from the reply text.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol

import httpx
from PIL import Image
from pydantic import BaseModel

from .providers.base import SIGNATURE_WEIGHTS
from .series import Series
from .storyboard import Shot

ZHIPU_CHAT_COMPLETIONS_URL = (
    "https://open.bigmodel.cn/api/paas/v4/chat/completions"
)
GLM_MODEL = "glm-4v-flash"

_JUDGE_PROMPT = """你是微短剧跨集角色一致性评审。第一张图是角色设定集的参考图（锁定形象），第二张图是某个镜头的生成结果。
请对比两图，仅输出一个 JSON 对象，不要输出任何其他文字，字段如下：
{"score": 0到1之间的小数,总体一致性, "face": 脸型五官一致性0到1, "hair": 发型发色一致性0到1, "outfit": 服装一致性0到1, "scene": 场景与运镜还原度0到1, "reason": "不超过30字的一句理由"}
score 低于 0.85 表示角色发生了不可接受的漂移。"""


# ---------------------------------------------------------------------------
# 角色设定集锁定：prompt contract
# ---------------------------------------------------------------------------


def build_subject_reference_prompt(shot: Shot, series: Series) -> str:
    """Assemble the locked-character prompt for one shot."""
    parts: list[str] = []
    for character_id in shot.characters:
        character = series.character(character_id)
        lock = "；".join(
            f"{key}：{value}" for key, value in character.appearance_lock.items()
        )
        parts.append(f"【{character.name}（{character.id}）】{lock}")
    if not parts:
        raise SystemExit(f"镜头 {shot.id} 未绑定角色，无法构建锁定提示词")
    parts.append(f"镜头：{shot.camera}。{shot.action}。")
    if shot.line:
        parts.append(f"台词情绪参考：「{shot.line}」（画面不输出字幕）")
    if series.style.palette_lock:
        parts.append("画面主色：" + "、".join(series.style.palette_lock))
    if series.style.negative_lock:
        parts.append("避免：" + "、".join(series.style.negative_lock))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Verdicts + judges
# ---------------------------------------------------------------------------


class ShotVerdict(BaseModel):
    """Structured outcome of one judged shot."""

    shot_id: str
    score: float
    dimensions: dict[str, float] = {}
    reason: str = ""
    judge: str = ""


class ConsistencyJudge(Protocol):
    """The gate's judge interface (live GLM-4V-Flash today, swappable later)."""

    name: str

    def score_shot(self, frame: Path, shot: Shot, series: Series) -> ShotVerdict:
        ...


class Glm4VFlashJudge:
    """Live judge: GLM-4V-Flash structured scoring via prompt-agreed JSON."""

    name = "glm-4v-flash"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ZHIPU_API_KEY", "")
        if not self.api_key:
            raise SystemExit(
                "GLM-4V-Flash 一致性评审需要 ZHIPU_API_KEY（replay 模式无需任何 key："
                "运行 dramaser demo）"
            )

    def score_shot(self, frame: Path, shot: Shot, series: Series) -> ShotVerdict:
        reference = self._reference_image(series, shot)
        content = [
            {"type": "image_url", "image_url": {"url": _to_data_url(reference)}},
            {"type": "image_url", "image_url": {"url": _to_data_url(frame)}},
            {"type": "text", "text": _JUDGE_PROMPT},
        ]
        payload = {
            "model": GLM_MODEL,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
        }
        response = httpx.post(
            ZHIPU_CHAT_COMPLETIONS_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=120.0,
        )
        if response.status_code != 200:
            raise SystemExit(
                f"GLM-4V-Flash 评审失败 HTTP {response.status_code}: {response.text[:300]}"
            )
        reply = response.json()["choices"][0]["message"]["content"]
        parsed = _parse_prompt_json(reply)
        return ShotVerdict(
            shot_id=shot.id,
            score=float(parsed.get("score", 0.0)),
            dimensions={
                key: float(parsed.get(key, 0.0))
                for key in ("face", "hair", "outfit", "scene")
            },
            reason=str(parsed.get("reason", ""))[:80],
            judge=self.name,
        )

    def _reference_image(self, series: Series, shot: Shot) -> Path:
        if not shot.characters:
            raise SystemExit(f"镜头 {shot.id} 未绑定角色，无法评审")
        character_id = shot.characters[0]
        character = series.character(character_id)
        if not character.reference_images:
            raise SystemExit(f"角色 {character.id} 缺少参考图，无法评审")
        candidates = []
        if series.source_dir is not None:
            candidates.append(series.source_dir / character.reference_images[0])
        from .series import resolve_assets_root

        candidates.append(resolve_assets_root() / "presets" / character.id / character.reference_images[0])
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        raise SystemExit(f"找不到角色参考图 {character.reference_images[0]}")


class ReplayJudge:
    """No-key demo judge: scores the render signature stamped in the artifact.

    The replay provider writes each frame's character parameter signature into
    the PNG metadata; this judge compares it against the locked baseline and
    maps the weighted distance to the same 0-1 score the live judge produces.
    It stands in for GLM-4V-Flash only on the deterministic demo path.
    """

    name = "replay"

    #: a 0.35 color distance (35% channel shift) or a 0.5 shape change
    #: counts as a full mismatch on that dimension
    _COLOR_FULL = 0.35
    _SHAPE_FULL = 0.5

    def __init__(self, baseline: dict[str, float] | None = None) -> None:
        self.baseline = baseline or {}

    def set_baseline(self, baseline: dict[str, float]) -> None:
        self.baseline = baseline

    def score_shot(self, frame: Path, shot: Shot, series: Series) -> ShotVerdict:
        signature = self._read_signature(frame)
        if signature is None:
            raise SystemExit(
                f"{frame.name} 缺少 dramaser_signature 元数据，无法回放评分"
            )
        score = self.signature_score(signature, self.baseline)
        return ShotVerdict(
            shot_id=shot.id,
            score=score,
            dimensions={"signature": round(score, 4)},
            reason="回放评分：渲染参数签名与锁定基准的加权距离",
            judge=self.name,
        )

    def signature_score(self, signature: dict[str, float], baseline: dict[str, float]) -> float:
        if not baseline:
            return 1.0
        total = 0.0
        weight_sum = 0.0
        for key, weight in SIGNATURE_WEIGHTS.items():
            if key not in baseline or key not in signature:
                continue
            weight_sum += weight
            delta = abs(float(signature[key]) - float(baseline[key]))
            full = self._SHAPE_FULL if key in ("face", "eye", "hair_len") else self._COLOR_FULL
            total += weight * min(1.0, delta / full)
        if weight_sum == 0:
            return 1.0
        return max(0.0, 1.0 - total / weight_sum)

    @staticmethod
    def _read_signature(frame: Path) -> dict[str, float] | None:
        try:
            with Image.open(frame) as image:
                raw = image.text.get("dramaser_signature") if hasattr(image, "text") else None
        except OSError:
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return {key: float(value) for key, value in data.items()}
        except (ValueError, TypeError):
            return None


# ---------------------------------------------------------------------------
# Frame extraction (video shots -> judgeable stills)
# ---------------------------------------------------------------------------


def extract_judge_frame(asset: Path, out_dir: Path) -> Path:
    """Return a still image for the judge; videos are sampled via ffmpeg."""
    if asset.suffix.lower() in (".png", ".jpg", ".jpeg"):
        return asset
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("评审视频需要 ffmpeg 二进制（PATH 中未找到）")
    out_path = out_dir / f"{asset.stem}_judge.png"
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-ss", "0.5", "-i", str(asset),
         "-frames:v", "1", str(out_path)],
        check=True,
    )
    return out_path


# ---------------------------------------------------------------------------
# Gate application
# ---------------------------------------------------------------------------


def apply_gate(shot: Shot, verdict: ShotVerdict, threshold: float) -> None:
    """Record a verdict into shot lineage (the single gate entry point)."""
    shot.lineage.mark(verdict.score, threshold)


def _parse_prompt_json(text: str) -> dict[str, Any]:
    """Extract the agreed JSON object from a vision-model reply."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except ValueError:
            pass
    raise SystemExit(f"无法从 GLM-4V-Flash 回复解析 JSON：{text[:300]}")


def _to_data_url(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"
