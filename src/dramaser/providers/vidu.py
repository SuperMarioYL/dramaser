"""Vidu subject-reference live provider.

Implements the m3 live generation loop: build the locked-character prompt from
the character sheet, submit an img2video task with the character reference
image (Vidu "主体参考"), poll the task, and download the resulting clip as
the shot asset.

Requires ``VIDU_API_KEY`` in the environment. The request/response schema
follows Vidu's public ent/v2 API as documented at build time; the plan flags
this adapter as schema-unverified until it runs against a live key, so the
wire format is isolated in :meth:`ViduProvider._create_task` /
:meth:`ViduProvider._poll_task` for easy correction.
"""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import httpx

from ..consistency import build_subject_reference_prompt
from .base import GenerationRequest, GenerationResult, params_hash

VIDU_BASE_URL = "https://api.vidu.com"
VIDU_MODEL = "viduq1"
POLL_INTERVAL_S = 5.0
TASK_TIMEOUT_S = 600.0


class ViduProvider:
    """Live generation via Vidu 主体参考 (character reference img2video)."""

    name = "vidu"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = VIDU_BASE_URL,
        model: str = VIDU_MODEL,
        resolution: str = "720p",
    ) -> None:
        self.api_key = api_key or os.environ.get("VIDU_API_KEY", "")
        if not self.api_key:
            raise SystemExit(
                "Vidu live 生成需要 VIDU_API_KEY（replay 模式无需任何 key："
                "运行 dramaser demo）"
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.resolution = resolution
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {self.api_key}"}, timeout=60.0
        )

    # -- public API ------------------------------------------------------------

    def generate(self, request: GenerationRequest) -> GenerationResult:
        prompt = build_subject_reference_prompt(request.shot, request.series)
        reference = self._reference_image(request)
        duration = "8" if request.shot.duration_s > 5 else "4"
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "images": [reference],
            "duration": duration,
            "resolution": self.resolution,
        }
        task_id = self._create_task(payload)
        video_url = self._poll_task(task_id)
        request.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._download(video_url, request.out_path)
        return GenerationResult(
            shot_id=request.shot.id,
            provider=self.name,
            asset=request.out_path,
            params_hash=params_hash(
                {
                    "provider": self.name,
                    "model": self.model,
                    "prompt": prompt,
                    "reference": _data_digest(reference),
                    "duration": duration,
                    "resolution": self.resolution,
                }
            ),
            meta={"task_id": task_id, "prompt": prompt},
        )

    # -- wire format (isolated for schema corrections) --------------------------

    def _create_task(self, payload: dict[str, Any]) -> str:
        response = self._client.post(f"{self.base_url}/ent/v2/img2video", json=payload)
        if response.status_code != 200:
            raise SystemExit(
                f"Vidu 创建任务失败 HTTP {response.status_code}: {response.text[:300]}"
            )
        data = response.json()
        task_id = _dig(data, "id") or _dig(data, "data", "id")
        if not task_id:
            raise SystemExit(f"Vidu 创建任务响应缺少任务 id：{str(data)[:300]}")
        return str(task_id)

    def _poll_task(self, task_id: str) -> str:
        deadline = time.monotonic() + TASK_TIMEOUT_S
        while time.monotonic() < deadline:
            response = self._client.get(f"{self.base_url}/ent/v2/tasks/{task_id}")
            if response.status_code != 200:
                raise SystemExit(
                    f"Vidu 查询任务失败 HTTP {response.status_code}: {response.text[:300]}"
                )
            data = response.json()
            state = _dig(data, "state") or _dig(data, "data", "state") or ""
            if state in ("success", "succeed", "completed"):
                url = (
                    _dig(data, "video_url")
                    or _dig(data, "data", "video_url")
                    or _dig(data, "videos", 0, "url")
                    or _dig(data, "data", "videos", 0, "url")
                )
                if not url:
                    raise SystemExit(f"Vidu 任务成功但缺少视频地址：{str(data)[:300]}")
                return str(url)
            if state in ("fail", "failed", "error"):
                raise SystemExit(f"Vidu 生成失败：{str(data)[:300]}")
            time.sleep(POLL_INTERVAL_S)
        raise SystemExit(f"Vidu 任务 {task_id} 超过 {TASK_TIMEOUT_S:.0f}s 未完成")

    def _download(self, url: str, out_path: Path) -> None:
        with self._client.stream("GET", url) as response:
            response.raise_for_status()
            with open(out_path, "wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)

    # -- helpers -----------------------------------------------------------------

    def _reference_image(self, request: GenerationRequest) -> str:
        """First reference image of the shot's lead character, as a data URI."""
        if not request.shot.characters:
            raise SystemExit(f"镜头 {request.shot.id} 未绑定角色，无法主体参考")
        character = request.series.character(request.shot.characters[0])
        if not character.reference_images or not request.series_dir:
            raise SystemExit(
                f"角色 {character.id} 缺少参考图（reference_images），无法主体参考"
            )
        ref_path = Path(request.series_dir) / character.reference_images[0]
        if not ref_path.is_file():
            raise SystemExit(f"角色参考图不存在：{ref_path}")
        return _to_data_uri(ref_path)


def _dig(data: Any, *keys: Any) -> Any:
    node = data
    for key in keys:
        if isinstance(node, dict):
            node = node.get(key)
        elif isinstance(node, list) and isinstance(key, int) and 0 <= key < len(node):
            node = node[key]
        else:
            return None
    return node


def _to_data_uri(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


def _data_digest(data_uri: str) -> str:
    import hashlib

    return hashlib.sha256(data_uri.encode("ascii")).hexdigest()[:16]
