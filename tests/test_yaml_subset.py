"""Block-style YAML subset: the reader must accept the shapes the writer emits
and the documented series.yaml hand-edit style (dramaser v0.3.0 fixes)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT / "src"))

from dramaser.series import yaml_dump, yaml_load  # noqa: E402


def test_sequence_item_first_key_nested_block_roundtrip() -> None:
    """yaml_dump can emit ``- k:`` + an indented block; yaml_load must read it."""
    tree = {"items": [{"k": {"sub": "v"}}, {"k2": "plain"}]}
    assert yaml_load(yaml_dump(tree)) == tree


def test_inline_comment_after_key_keeps_nested_block() -> None:
    """The README template annotates a key that owns a nested mapping."""
    doc = (
        "characters:\n"
        "  - id: ayao\n"
        "    name: 阿瑶\n"
        "    appearance_lock:      # 角色设定集锁定契约\n"
        "      脸型: 鹅蛋脸\n"
    )
    assert yaml_load(doc) == {
        "characters": [
            {"id": "ayao", "name": "阿瑶", "appearance_lock": {"脸型": "鹅蛋脸"}}
        ]
    }


def test_hash_inside_quoted_value_is_not_comment() -> None:
    """Comment stripping must respect quotes: '#' in a value stays content."""
    tree = {"palette": ["#2b2340", "#a98f6f"], "note": "a # b"}
    assert yaml_load(yaml_dump(tree)) == tree
