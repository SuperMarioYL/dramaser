"""SeriesAsset: the versionable "series project" document.

A series project is one ``series.yaml`` plus an assets directory, designed to
be git-friendly: character sheets, platform rules, storyboards and shot
lineage all live in a single reviewable file.

Because the plan's dependency set is deliberately minimal, this module ships
its own YAML reader/writer for the block-style subset the documents use
(nested mappings, block/flow sequences of scalars, quoted or plain scalars,
comments). Files written by dramaser are valid YAML for any standard parser;
hand edits should follow the generated style — anything exotic (anchors, flow
mappings, multi-line literals) raises :class:`YamlSubsetError` with a clear
message instead of mis-parsing.
"""

from __future__ import annotations

import importlib.resources
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from .storyboard import (
    Lineage,
    PlatformPreset,
    Shot,
    SubtitleSafeZone,
)

# ---------------------------------------------------------------------------
# YAML subset reader/writer
# ---------------------------------------------------------------------------

_INDENT = 2


class YamlSubsetError(ValueError):
    """The document uses YAML constructs outside dramaser's supported subset."""


class _Scalar:
    """A raw scalar token that must not be re-interpreted as a nested block."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _split_key(line: str) -> tuple[str, str]:
    """Split ``key: rest`` at the first colon followed by space or end-of-line."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if i + 1 == len(line) or line[i + 1] == " ":
                key = line[:i].strip()
                rest = line[i + 1 :].strip()
                if not key:
                    raise YamlSubsetError(f"空键名：{line!r}")
                return key, rest
    raise YamlSubsetError(
        f"无法解析键值行（缺少「键: 值」冒号）：{line!r}"
    )


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text.startswith("["):
        return _parse_flow_sequence(text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return _unquote(text)
    # plain scalar: trailing comments only when preceded by whitespace
    if " #" in text:
        text = text.split(" #", 1)[0].rstrip()
    if text == "":
        return None
    if text in ("~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    return text


def _unquote(token: str) -> str:
    quote = token[0]
    body = token[1:-1]
    if quote == "'":
        return body.replace("''", "'")
    return (
        body.replace('\\"', '"')
        .replace("\\\\", "\\")
        .replace("\\n", "\n")
    )


def _parse_flow_sequence(text: str) -> list[Any]:
    text = text.strip()
    if not text.endswith("]"):
        raise YamlSubsetError(f"流式序列缺少右括号：{text!r}")
    body = text[1:-1].strip()
    if not body:
        return []
    items: list[str] = []
    current: list[str] = []
    quote = ""
    for ch in body:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            current.append(ch)
        elif ch == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(ch)
    items.append("".join(current))
    return [_parse_scalar(item) for item in items if item.strip()]


def yaml_load(text: str) -> dict[str, Any]:
    """Parse a block-style YAML subset document into a dict."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            raise YamlSubsetError("请使用空格缩进（检测到 Tab）")
        lines.append(line)
    if not lines:
        return {}
    value, next_i = _parse_block(lines, 0, _indent_of(lines[0]))
    if next_i != len(lines):
        raise YamlSubsetError(f"存在无法归组的行：{lines[next_i]!r}")
    if not isinstance(value, dict):
        raise YamlSubsetError("文档顶层必须是映射（键值对）")
    return value


def _parse_block(lines: list[str], i: int, indent: int) -> tuple[Any, int]:
    marker = lines[i][indent:]
    if marker == "-" or marker.startswith("- "):
        return _parse_sequence(lines, i, indent)
    return _parse_mapping(lines, i, indent, first_line=marker)


def _parse_mapping(
    lines: list[str], i: int, indent: int, first_line: str | None = None
) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    if first_line is not None:
        # a "- key: value" sequence item: consume the synthetic first pair
        # without advancing past the current line
        key, rest = _split_key(first_line)
        result[key] = _parse_scalar(rest) if rest else None
    while i < len(lines):
        line = lines[i]
        if _indent_of(line) != indent:
            if _indent_of(line) < indent:
                break
            raise YamlSubsetError(f"缩进不一致的行：{line!r}")
        content = line[indent:]
        if content == "-" or content.startswith("- "):
            break
        key, rest = _split_key(content)
        if rest:
            result[key] = _parse_scalar(rest)
            i += 1
            continue
        # empty value: nested block or null
        if i + 1 < len(lines) and _indent_of(lines[i + 1]) > indent:
            value, i = _parse_block(lines, i + 1, _indent_of(lines[i + 1]))
            result[key] = value
        else:
            result[key] = None
            i += 1
    return result, i


def _parse_sequence(lines: list[str], i: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while i < len(lines):
        line = lines[i]
        if _indent_of(line) != indent:
            break
        content = line[indent:]
        if not (content == "-" or content.startswith("- ")):
            break
        rest = content[1:].strip()
        if not rest:
            if i + 1 < len(lines) and _indent_of(lines[i + 1]) > indent:
                value, i = _parse_block(lines, i + 1, _indent_of(lines[i + 1]))
                items.append(value)
            else:
                items.append(None)
                i += 1
            continue
        if _looks_like_mapping_start(rest):
            value, i = _parse_mapping(lines, i + 1, indent + _INDENT, first_line=rest)
            items.append(value)
        else:
            items.append(_parse_scalar(rest))
            i += 1
    return items, i


def _looks_like_mapping_start(text: str) -> bool:
    probe = text.strip()
    if probe.startswith(("[", "'", '"')):
        return False
    in_single = in_double = False
    for i, ch in enumerate(probe):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == ":" and not in_single and not in_double:
            if i + 1 == len(probe) or probe[i + 1] == " ":
                return True
    return False


def yaml_dump(data: Any) -> str:
    """Serialize a dict/list tree in the canonical block-style subset."""
    return "\n".join(_dump_node(data, 0)) + "\n"


def _dump_node(node: Any, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = str(key)
            if ": " in key_text or key_text.endswith(":"):
                raise YamlSubsetError(f"键名包含冒号，无法安全写出：{key_text!r}")
            if isinstance(value, dict) and value:
                lines.append(f"{pad}{key_text}:")
                lines.extend(_dump_node(value, indent + _INDENT))
            elif isinstance(value, list) and value and all(
                not isinstance(v, (dict, list)) for v in value
            ):
                lines.append(f"{pad}{key_text}: [{_join_flow(value)}]")
            elif isinstance(value, list) and value:
                lines.append(f"{pad}{key_text}:")
                for item in value:
                    lines.extend(_dump_list_item(item, indent + _INDENT))
            elif isinstance(value, (dict, list)):
                lines.append(f"{pad}{key_text}: " + ("{}" if isinstance(value, dict) else "[]"))
            else:
                lines.append(f"{pad}{key_text}: {_format_scalar(value)}")
    elif isinstance(node, list):
        for item in node:
            lines.extend(_dump_list_item(item, indent))
    else:
        lines.append(f"{pad}{_format_scalar(node)}")
    return lines


def _dump_list_item(item: Any, indent: int) -> list[str]:
    pad = " " * indent
    if isinstance(item, dict):
        if not item:
            return [f"{pad}- {{}}"]
        inner = _dump_node(item, indent + _INDENT)
        first = inner[0].lstrip()
        rest = inner[1:]
        return [f"{pad}- {first}", *rest]
    if isinstance(item, list):
        if not item:
            return [f"{pad}- []"]
        inner = _dump_list_item(item[0], indent + _INDENT)
        # nested list-of-list is not part of the schema; flatten defensively
        raise YamlSubsetError("不支持嵌套的序列作为序列项")
    return [f"{pad}- {_format_scalar(item)}"]


def _join_flow(items: list[Any]) -> str:
    return ", ".join(_format_scalar(item) for item in items)


_PLAIN_INT = re.compile(r"-?\d+$")
_PLAIN_FLOAT = re.compile(r"-?\d+\.\d+$")


def _format_scalar(value: Any) -> str:
    if value is None:
        return "~"
    if value is True:
        return "true"
    if value is False:
        return "false"
    text = str(value)
    if isinstance(value, (int, float)):
        return text
    if _needs_quotes(text):
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return text


def _needs_quotes(text: str) -> bool:
    if text == "":
        return True
    if text in ("~", "null", "true", "false", "True", "False"):
        return True
    if _PLAIN_INT.fullmatch(text) or _PLAIN_FLOAT.fullmatch(text):
        return True
    if text != text.strip():
        return True
    if text[0] in "-?:#&*!|>%@`{}[],'\"" :
        return True
    if ": " in text or " #" in text or text.endswith(":"):
        return True
    if text.startswith("- "):
        return True
    return False


# ---------------------------------------------------------------------------
# Series model
# ---------------------------------------------------------------------------


class Character(BaseModel):
    """角色设定集 entry: the cross-episode lock contract for one character."""

    id: str
    name: str
    role: str = ""
    appearance_lock: dict[str, str] = Field(default_factory=dict)
    reference_images: list[str] = Field(default_factory=list)
    voice: str = ""


class StyleLock(BaseModel):
    """Series-wide style locks shared by every shot prompt."""

    palette_lock: list[str] = Field(default_factory=list)
    negative_lock: list[str] = Field(default_factory=list)


class Gates(BaseModel):
    """Machine-verifiable quality gates."""

    consistency_threshold: float = 0.85


class Episode(BaseModel):
    id: str
    title: str = ""
    shots: list[Shot] = Field(default_factory=list)


class Series(BaseModel):
    """The SeriesAsset document: characters + style + platform + storyboards."""

    title: str = ""
    genre: str = ""
    characters: list[Character] = Field(default_factory=list)
    style: StyleLock = Field(default_factory=StyleLock)
    platform: PlatformPreset = Field(default_factory=PlatformPreset)
    gates: Gates = Field(default_factory=Gates)
    episodes: list[Episode] = Field(default_factory=list)

    _source_dir: Path | None = PrivateAttr(default=None)

    @property
    def source_dir(self) -> Path | None:
        """Directory the series document was loaded from, if any."""
        return self._source_dir

    # -- loading / saving ---------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Series":
        path = Path(path)
        data = yaml_load(path.read_text(encoding="utf-8"))
        try:
            series = cls.model_validate(data)
        except ValidationError as exc:
            raise SystemExit(f"series.yaml 校验失败（{path}）：\n{exc}") from exc
        series._source_dir = path.parent
        series.validate_invariants()
        return series

    def save(self, path: str | Path) -> None:
        Path(path).write_text(yaml_dump(self.to_doc()), encoding="utf-8")

    def to_doc(self) -> dict[str, Any]:
        """Serializable tree; pending lineages are omitted to keep files lean."""
        doc = self.model_dump(mode="json")
        pending = Lineage().model_dump(mode="json")
        for episode in doc["episodes"]:
            for shot in episode["shots"]:
                if shot.get("lineage") == pending:
                    shot.pop("lineage")
        return doc

    # -- lookups ------------------------------------------------------------

    def character(self, character_id: str) -> Character:
        for character in self.characters:
            if character.id == character_id:
                return character
        raise KeyError(f"未定义角色 {character_id!r}")

    def episode(self, episode_id: str) -> Episode:
        for episode in self.episodes:
            if episode.id == episode_id:
                return episode
        raise KeyError(f"未定义分集 {episode_id!r}")

    def all_shots(self) -> list[Shot]:
        return [shot for episode in self.episodes for shot in episode.shots]

    def shot(self, shot_id: str) -> Shot:
        for shot in self.all_shots():
            if shot.id == shot_id:
                return shot
        raise KeyError(f"未定义镜头 {shot_id!r}")

    def validate_invariants(self) -> None:
        shot_ids = [shot.id for shot in self.all_shots()]
        if len(shot_ids) != len(set(shot_ids)):
            raise SystemExit("series.yaml 存在重复的镜头 id")
        for shot in self.all_shots():
            for character_id in shot.characters:
                try:
                    self.character(character_id)
                except KeyError as exc:
                    raise SystemExit(f"镜头 {shot.id} 引用了未定义角色 {character_id}") from exc
            if shot.duration_s <= 0:
                raise SystemExit(f"镜头 {shot.id} 的时长必须为正数")


# ---------------------------------------------------------------------------
# Preset + assets resolution
# ---------------------------------------------------------------------------


def resolve_assets_root() -> Path:
    """Locate the preset/replay assets directory.

    Order: ``$DRAMASER_ASSETS`` → the repo checkout containing this file →
    ``./assets`` → the copy bundled inside the installed package (so
    ``uvx dramaser demo`` works without a checkout).
    """
    env = os.environ.get("DRAMASER_ASSETS")
    if env:
        root = Path(env)
        if not (root / "presets").is_dir():
            raise SystemExit(f"DRAMASER_ASSETS 指向的目录缺少 presets/：{root}")
        return root
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        candidate = parent / "assets"
        if (candidate / "presets").is_dir() and (candidate / "demo-replay").is_dir():
            return candidate
    cwd_candidate = Path.cwd() / "assets"
    if (cwd_candidate / "presets").is_dir():
        return cwd_candidate
    bundled = Path(str(importlib.resources.files("dramaser"))) / "bundled"
    if (bundled / "presets").is_dir():
        return bundled
    raise SystemExit(
        "找不到预置资产目录（presets/ 与 demo-replay/）。"
        "请在仓库根目录运行，或设置 DRAMASER_ASSETS。"
    )


def list_presets() -> list[str]:
    presets_dir = resolve_assets_root() / "presets"
    return sorted(p.name for p in presets_dir.iterdir() if (p / "series.yaml").is_file())


def load_preset(name: str) -> tuple[Series, Path]:
    """Load a bundled preset character sheet; returns (series, preset_dir)."""
    preset_dir = resolve_assets_root() / "presets" / name
    if not (preset_dir / "series.yaml").is_file():
        available = ", ".join(list_presets()) or "（无）"
        raise SystemExit(f"找不到预置角色集 {name!r}，可用：{available}")
    return Series.load(preset_dir / "series.yaml"), preset_dir
