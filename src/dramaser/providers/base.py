"""Generation provider abstraction: replay provider + live provider interface.

The provider is the execution layer behind a shot: it turns a
:class:`GenerationRequest` (shot + character locks + platform rules) into a
concrete media asset on disk, tagged with the params hash that feeds shot
lineage.

Two implementations exist:

- :class:`ReplayProvider` — the deterministic, no-key demo engine. It renders
  stylized 9:16 frames from the preset replay fixtures (``assets/demo-replay``)
  so ``dramaser demo`` is fully reproducible without any API key. Its output
  is a deliberate visualization of drift vs. lock, not model output.
- :class:`dramaser.providers.vidu.ViduProvider` — the live Vidu subject-
  reference provider (requires ``VIDU_API_KEY``).
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from PIL import Image, ImageDraw, PngImagePlugin

from ..series import Series, resolve_assets_root, yaml_load
from ..storyboard import PlatformPreset, Shot

#: Replay frames render at 720x1280 (9:16); the draft canvas stays 1080x1920.
REPLAY_FRAME_SIZE = (720, 1280)

#: Camera framing presets -> character block scale.
FRAMING_SCALE: dict[str, float] = {
    "远景": 0.42,
    "中景": 0.62,
    "近景": 0.82,
    "特写": 1.05,
}
DEFAULT_FRAMING = 0.62


# ---------------------------------------------------------------------------
# Request / result contracts
# ---------------------------------------------------------------------------


@dataclass
class GenerationRequest:
    """Everything a provider needs to produce one shot asset."""

    shot: Shot
    series: Series
    out_path: Path
    series_dir: Path | None = None
    run_id: str = ""
    mode: str = "locked"  # replay engine only: "locked" | "drift"


@dataclass
class GenerationResult:
    """Outcome of one generation, feeding :class:`~dramaser.storyboard.Lineage`."""

    shot_id: str
    provider: str
    asset: Path
    params_hash: str
    meta: dict[str, Any] = field(default_factory=dict)


class Provider(Protocol):
    """The execution-layer interface every generation backend implements."""

    name: str

    def generate(self, request: GenerationRequest) -> GenerationResult:
        ...


def make_provider(name: str, *, fixtures_dir: Path | None = None) -> Provider:
    if name == "replay":
        return ReplayProvider(fixtures_dir)
    if name == "vidu":
        from .vidu import ViduProvider

        return ViduProvider()
    raise SystemExit(
        f"未知 provider {name!r}；v0.1 提供 replay（无 key 回放）与 vidu（主体参考）"
    )


def params_hash(payload: dict[str, Any]) -> str:
    """Stable short hash of the canonical generation parameters."""
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Replay fixtures
# ---------------------------------------------------------------------------


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.strip()
    return tuple(int(value[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def _shift_hue(color: tuple[int, int, int], degrees: float) -> tuple[int, int, int]:
    if not degrees:
        return color
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in color))
    r, g, b = colorsys.hls_to_rgb((h + degrees / 360.0) % 1.0, l, s)
    return (round(r * 255), round(g * 255), round(b * 255))


def _shift_lightness(color: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    if not amount:
        return color
    h, l, s = colorsys.rgb_to_hls(*(c / 255.0 for c in color))
    r, g, b = colorsys.hls_to_rgb(h, min(1.0, max(0.0, l + amount)), s)
    return (round(r * 255), round(g * 255), round(b * 255))


@dataclass
class CharacterParams:
    """The concrete render state of one character in one frame."""

    skin: tuple[int, int, int]
    hair: tuple[int, int, int]
    eyes: tuple[int, int, int]
    outfit: tuple[int, int, int]
    coat: tuple[int, int, int]
    blush: tuple[int, int, int]
    face_scale: float = 1.0
    eye_scale: float = 1.0
    hair_length: float = 1.0

    def signature(self) -> dict[str, float]:
        """Flat numeric signature used by the replay consistency judge."""
        flat: dict[str, float] = {}
        for key in ("skin", "hair", "eyes", "outfit", "coat", "blush"):
            channels = getattr(self, key)
            flat[key] = sum(c * c for c in channels) ** 0.5 / 441.7
        flat["face"] = self.face_scale
        flat["eye"] = self.eye_scale
        flat["hair_len"] = self.hair_length
        return flat


#: Signature dimensions the replay judge weighs (color + shape contract).
SIGNATURE_WEIGHTS: dict[str, float] = {
    "hair": 0.30,
    "outfit": 0.20,
    "coat": 0.10,
    "skin": 0.15,
    "blush": 0.05,
    "eyes": 0.05,
    "face": 0.10,
    "eye": 0.03,
    "hair_len": 0.02,
}


@dataclass
class SceneSpec:
    """Per-shot scene rendering parameters from the replay fixtures."""

    sky_top: tuple[int, int, int]
    sky_bottom: tuple[int, int, int]
    prop: str = "none"
    seed: int = 0


class ReplayProvider:
    """Deterministic offline provider used by the no-key demo path.

    Reads ``assets/demo-replay/replay.yaml`` (character render specs, per-shot
    scenes, and the per-episode drift schedule for the un-locked variant) and
    renders every shot with Pillow. Same fixtures in, same frames out — the
    demo is reproducible by construction.
    """

    name = "replay"

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self.fixtures_dir = fixtures_dir or (resolve_assets_root() / "demo-replay")
        fixture_file = self.fixtures_dir / "replay.yaml"
        if not fixture_file.is_file():
            raise SystemExit(f"找不到回放夹具 {fixture_file}")
        self.fixture: dict[str, Any] = yaml_load(fixture_file.read_text(encoding="utf-8"))
        self._render_specs: dict[str, dict[str, str]] = self.fixture.get("characters", {})
        self._scenes: dict[str, dict[str, Any]] = self.fixture.get("scenes", {})
        self._drift: dict[str, dict[str, Any]] = self.fixture.get("drift", {})

    # -- parameter assembly -------------------------------------------------

    def _base_params(self, request: GenerationRequest) -> CharacterParams:
        character_id = request.shot.characters[0] if request.shot.characters else ""
        spec = self._render_specs.get(character_id)
        if spec is None:
            raise SystemExit(
                f"回放夹具缺少角色 {character_id!r} 的 render 参数"
            )
        return CharacterParams(
            skin=_hex_to_rgb(spec["skin"]),
            hair=_hex_to_rgb(spec["hair"]),
            eyes=_hex_to_rgb(spec["eyes"]),
            outfit=_hex_to_rgb(spec["outfit"]),
            coat=_hex_to_rgb(spec["coat"]),
            blush=_hex_to_rgb(spec.get("blush", "#e8a8a0")),
        )

    def character_params(self, request: GenerationRequest) -> CharacterParams:
        """Final character params for a shot, drift schedule and all."""
        params = self._base_params(request)
        if request.mode != "drift":
            return params
        episode_drift = self._drift.get(request.shot.episode_id, {})
        params.hair = _shift_hue(params.hair, float(episode_drift.get("hair_hue", 0)))
        params.hair = _shift_lightness(params.hair, float(episode_drift.get("hair_light", 0)))
        params.outfit = _shift_hue(params.outfit, float(episode_drift.get("outfit_hue", 0)))
        params.outfit = _shift_lightness(params.outfit, float(episode_drift.get("outfit_light", 0)))
        params.coat = _shift_hue(params.coat, float(episode_drift.get("coat_hue", 0)))
        params.coat = _shift_lightness(params.coat, float(episode_drift.get("coat_light", 0)))
        params.skin = _shift_lightness(params.skin, float(episode_drift.get("skin", 0)))
        params.face_scale += float(episode_drift.get("face", 0))
        params.eye_scale += float(episode_drift.get("eye", 0))
        params.hair_length += float(episode_drift.get("hair_len", 0))
        # deterministic per-shot jitter: un-locked generation re-rolls details
        rng = random.Random(_shot_seed(request.shot.id))
        params.hair = _shift_hue(params.hair, rng.uniform(-8, 8))
        params.face_scale += rng.uniform(-0.02, 0.02)
        params.eye_scale += rng.uniform(-0.04, 0.04)
        params.outfit = _shift_lightness(params.outfit, rng.uniform(-0.03, 0.03))
        return params

    def scene_spec(self, shot: Shot) -> SceneSpec:
        scene = self._scenes.get(shot.id, {})
        sky = scene.get("sky", ["#20263f", "#46507a"])
        return SceneSpec(
            sky_top=_hex_to_rgb(sky[0]),
            sky_bottom=_hex_to_rgb(sky[1]),
            prop=str(scene.get("prop", "none")),
            seed=_shot_seed(shot.id),
        )

    # -- Provider API ---------------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        params = self.character_params(request)
        scene = self.scene_spec(request.shot)
        framing = FRAMING_SCALE.get(request.shot.camera, DEFAULT_FRAMING)
        image = render_frame(params, scene, framing, REPLAY_FRAME_SIZE)
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        signature = params.signature()
        # stamp the render signature into the artifact so the replay judge can
        # score it from the file alone, exactly like the live judge scores frames
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("dramaser_signature", json.dumps(signature, ensure_ascii=False))
        png_info.add_text("dramaser_mode", request.mode)
        image.save(request.out_path, pnginfo=png_info)
        return GenerationResult(
            shot_id=request.shot.id,
            provider=self.name,
            asset=request.out_path,
            params_hash=params_hash(
                {
                    "provider": self.name,
                    "mode": request.mode,
                    "shot": request.shot.id,
                    "signature": signature,
                    "scene": [scene.sky_top, scene.sky_bottom, scene.prop],
                    "framing": framing,
                }
            ),
            meta={
                "signature": signature,
                "mode": request.mode,
                "framing": framing,
            },
        )


def _shot_seed(shot_id: str) -> int:
    return int(hashlib.sha256(shot_id.encode("utf-8")).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Stylized frame renderer
# ---------------------------------------------------------------------------


def _vertical_gradient(size: tuple[int, int], top: tuple[int, int, int],
                       bottom: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    for y in range(height):
        t = y / max(1, height - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        ImageDraw.Draw(image).line([(0, y), (width, y)], fill=color)
    return image


def _draw_prop(draw: ImageDraw.ImageDraw, size: tuple[int, int], scene: SceneSpec) -> None:
    """One simple, seeded scene prop per shot."""
    width, height = size
    rng = random.Random(scene.seed)
    if scene.prop == "rain":
        for _ in range(90):
            x = rng.randrange(width)
            y = rng.randrange(height)
            length = rng.randint(18, 42)
            shade = rng.randint(150, 210)
            draw.line(
                [(x, y), (x - 6, y + length)],
                fill=(shade, shade, min(255, shade + 25)),
                width=1,
            )
    elif scene.prop == "city":
        horizon = int(height * 0.66)
        draw.rectangle([0, horizon, width, height], fill=(18, 20, 34))
        for _ in range(26):
            x = rng.randrange(width)
            w = rng.randint(24, 70)
            h = rng.randint(60, 240)
            draw.rectangle(
                [x, horizon - h, x + w, horizon], fill=(28, 30, 48)
            )
            for _ in range(6):
                wx = x + rng.randint(4, max(5, w - 8))
                wy = horizon - h + rng.randint(6, max(7, h - 12))
                draw.rectangle(
                    [wx, wy, wx + 5, wy + 7], fill=(240, 214, 140)
                )
    elif scene.prop == "window":
        margin = int(width * 0.08)
        top = int(height * 0.12)
        bottom = int(height * 0.52)
        draw.rectangle(
            [margin, top, width - margin, bottom],
            outline=(232, 226, 210), width=6,
        )
        draw.line(
            [width // 2, top, width // 2, bottom], fill=(232, 226, 210), width=5
        )
        for _ in range(24):
            x = rng.randrange(margin + 10, width - margin - 10)
            y = rng.randrange(top + 10, bottom - 10)
            shade = rng.randint(120, 180)
            draw.point((x, y), fill=(shade, shade, shade + 20))
    elif scene.prop == "counter":
        y = int(height * 0.72)
        draw.rectangle([0, y, width, height], fill=(52, 38, 30))
        draw.rectangle([0, y, width, y + 10], fill=(74, 56, 44))
        cup_w, cup_h = 90, 70
        draw.rounded_rectangle(
            [width - 200, y - cup_h, width - 200 + cup_w, y],
            radius=10, fill=(236, 230, 220),
        )
        draw.ellipse(
            [width - 200 + 14, y - cup_h + 8, width - 200 + cup_w - 14, y - cup_h + 26],
            fill=(94, 66, 50),
        )
    elif scene.prop == "phone":
        pw, ph = 150, 300
        x0, y0 = (width - pw) // 2, int(height * 0.30)
        draw.rounded_rectangle(
            [x0, y0, x0 + pw, y0 + ph], radius=24, fill=(16, 18, 28),
            outline=(210, 205, 195), width=4,
        )
        draw.rectangle(
            [x0 + 16, y0 + 60, x0 + pw - 16, y0 + 130], fill=(120, 176, 120)
        )
        for i in range(4):
            draw.rectangle(
                [x0 + 16, y0 + 150 + i * 28, x0 + pw - 40 - rng.randint(0, 40), y0 + 162 + i * 28],
                fill=(70, 74, 92),
            )
    elif scene.prop == "lights":
        for _ in range(16):
            x = rng.randrange(width)
            y = rng.randrange(int(height * 0.55))
            r = rng.randint(4, 10)
            warm = rng.randint(200, 255)
            draw.ellipse(
                [x - r, y - r, x + r, y + r],
                fill=(warm, min(255, warm - 30), 120),
            )
    elif scene.prop == "crowd":
        horizon = int(height * 0.7)
        for _ in range(18):
            x = rng.randrange(width)
            h = rng.randint(60, 120)
            w = int(h * 0.45)
            shade = rng.randint(40, 90)
            draw.rounded_rectangle(
                [x, horizon - h, x + w, horizon], radius=w // 2,
                fill=(shade, shade, shade + 12),
            )


def render_frame(
    params: CharacterParams,
    scene: SceneSpec,
    framing: float,
    size: tuple[int, int] = REPLAY_FRAME_SIZE,
) -> Image.Image:
    """Render one stylized 9:16 frame.

    The character is always drawn at the same anchor with the same pose; only
    the locked parameter set (colors, face/eye scale, hair length), the scene
    and the camera framing vary. That is what makes drift visible in the demo
    GIF and scorable by the replay judge.
    """
    width, height = size
    image = _vertical_gradient(size, scene.sky_top, scene.sky_bottom)
    draw = ImageDraw.Draw(image)
    _draw_prop(draw, size, scene)

    scale = framing
    cx = width / 2
    head_cy = height * 0.36
    face_rx = 74 * scale * params.face_scale
    face_ry = 94 * scale * params.face_scale
    hair_rx = face_rx * 1.22
    hair_top = head_cy - face_ry * 1.30
    shoulder_y = head_cy + face_ry * 1.65
    torso_half = 168 * scale
    hair_tip = shoulder_y + 210 * scale * params.hair_length

    # back hair
    draw.polygon(
        [
            (cx - hair_rx, head_cy - face_ry * 0.2),
            (cx + hair_rx, head_cy - face_ry * 0.2),
            (cx + hair_rx * 0.92, hair_tip),
            (cx - hair_rx * 0.92, hair_tip),
        ],
        fill=params.hair,
    )
    draw.ellipse(
        [cx - hair_rx, hair_top, cx + hair_rx, head_cy + face_ry * 1.15],
        fill=params.hair,
    )
    # torso + coat
    draw.polygon(
        [
            (cx - torso_half, height),
            (cx - torso_half * 0.72, shoulder_y),
            (cx + torso_half * 0.72, shoulder_y),
            (cx + torso_half, height),
        ],
        fill=params.outfit,
    )
    draw.polygon(
        [
            (cx - torso_half, height),
            (cx - torso_half * 0.98, shoulder_y + 46 * scale),
            (cx - torso_half * 0.45, shoulder_y + 8 * scale),
            (cx - torso_half * 0.30, height),
        ],
        fill=params.coat,
    )
    draw.polygon(
        [
            (cx + torso_half, height),
            (cx + torso_half * 0.98, shoulder_y + 46 * scale),
            (cx + torso_half * 0.45, shoulder_y + 8 * scale),
            (cx + torso_half * 0.30, height),
        ],
        fill=params.coat,
    )
    # neck + face
    draw.rectangle(
        [cx - 26 * scale, head_cy + face_ry * 0.7, cx + 26 * scale, shoulder_y + 8],
        fill=_shift_lightness(params.skin, -0.05),
    )
    draw.ellipse(
        [cx - face_rx, head_cy - face_ry, cx + face_rx, head_cy + face_ry],
        fill=params.skin,
    )
    # bangs
    draw.polygon(
        [
            (cx - hair_rx, head_cy - face_ry * 0.55),
            (cx + hair_rx, head_cy - face_ry * 0.55),
            (cx + face_rx * 0.55, head_cy - face_ry * 0.18),
            (cx, head_cy - face_ry * 0.30),
            (cx - face_rx * 0.55, head_cy - face_ry * 0.18),
        ],
        fill=params.hair,
    )
    draw.ellipse(
        [cx - hair_rx, hair_top, cx + hair_rx, head_cy - face_ry * 0.25],
        fill=params.hair,
    )
    # eyes
    eye_dy = head_cy + face_ry * 0.02
    eye_dx = face_rx * 0.42
    eye_w = 17 * scale * params.eye_scale
    eye_h = 21 * scale * params.eye_scale
    for sign in (-1, 1):
        ex = cx + sign * eye_dx
        draw.ellipse(
            [ex - eye_w, eye_dy - eye_h, ex + eye_w, eye_dy + eye_h],
            fill=params.eyes,
        )
        draw.ellipse(
            [ex - eye_w * 0.35, eye_dy - eye_h * 0.55, ex + eye_w * 0.05, eye_dy - eye_h * 0.05],
            fill=(245, 244, 240),
        )
        # brow
        brow_y = eye_dy - eye_h - 14 * scale
        draw.arc(
            [ex - eye_w * 1.3, brow_y - 10 * scale, ex + eye_w * 1.3, brow_y + 10 * scale],
            start=200, end=340, fill=params.hair, width=max(2, int(4 * scale)),
        )
    # blush
    for sign in (-1, 1):
        bx = cx + sign * face_rx * 0.62
        by = head_cy + face_ry * 0.32
        r = 13 * scale
        draw.ellipse([bx - r, by - r * 0.6, bx + r, by + r * 0.6], fill=params.blush)
    # mouth
    mouth_y = head_cy + face_ry * 0.55
    draw.arc(
        [cx - 20 * scale, mouth_y - 12 * scale, cx + 20 * scale, mouth_y + 16 * scale],
        start=15, end=165, fill=(150, 84, 76), width=max(2, int(4 * scale)),
    )
    # signature accessory: small star earring on the left ear
    ear_x, ear_y = cx + face_rx * 0.94, head_cy + face_ry * 0.28
    star_r = 9 * scale
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        radius = star_r if i % 2 == 0 else star_r * 0.45
        points.append((ear_x + radius * math.cos(angle), ear_y - radius * math.sin(angle)))
    draw.polygon(points, fill=(226, 232, 240))

    return image
