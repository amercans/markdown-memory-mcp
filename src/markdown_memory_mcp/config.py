"""Configuration: defaults, ``.markdown-memory.toml`` and vault location."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CONFIG_NAME = ".markdown-memory.toml"
LEGACY_CONFIG_NAME = ".vault-recall.toml"  # read for one release after the rename
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class ConfigError(Exception):
    """Invalid configuration or vault location."""


@dataclass(frozen=True)
class Config:
    vault: Path
    index_files: tuple[str, ...] = ("MEMORY.md",)
    exclude: tuple[str, ...] = (".obsidian/**", ".trash/**")
    lint_exclude: tuple[str, ...] = ()
    model: str = DEFAULT_MODEL
    k: int = 3
    min_similarity: float = 0.30
    relative_margin: float = 0.08
    strong_similarity: float = 0.55
    required_fields: tuple[str, ...] = ("name", "description")
    budget_soft: int = 13000
    budget_hard: int = 15000
    max_index_line: int = 200
    hook_max_updates: int = 20
    duplicate_similarity: float = 0.92


_KEYS = {
    "index_files",
    "exclude",
    "lint_exclude",
    "model",
    "k",
    "min_similarity",
    "relative_margin",
    "strong_similarity",
    "required_fields",
    "index_budget_bytes",
    "max_index_line",
    "hook_max_updates",
    "duplicate_similarity",
}


def resolve_vault(explicit: str | None, env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = explicit or env.get("MARKDOWN_MEMORY_VAULT")
    path = Path(raw).expanduser() if raw else Path.cwd()
    path = path.resolve()
    if not path.is_dir():
        raise ConfigError(f"vault is not a directory: {path}")
    return path


def _str_list(data: dict[str, Any], key: str) -> tuple[str, ...] | None:
    if key not in data:
        return None
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} must be a list of strings")
    return tuple(value)


def _number(data: dict[str, Any], key: str, kind: type, lo: float, hi: float | None = None):
    if key not in data:
        return None
    value = data[key]
    ok = isinstance(value, kind) or (kind is float and isinstance(value, int))
    if isinstance(value, bool) or not ok:
        article = "an" if kind is int else "a"
        raise ConfigError(f"{key} must be {article} {kind.__name__}")
    if value < lo or (hi is not None and value > hi):
        bound = f">= {lo}" if hi is None else f"between {lo} and {hi}"
        raise ConfigError(f"{key} must be {bound}")
    return kind(value)


def load_config(
    vault: str | None = None,
    config_path: str | None = None,
    env: dict[str, str] | None = None,
) -> Config:
    root = resolve_vault(vault, env)
    path = Path(config_path).expanduser() if config_path else root / CONFIG_NAME
    if not config_path and not path.is_file() and (root / LEGACY_CONFIG_NAME).is_file():
        path = root / LEGACY_CONFIG_NAME
        print(
            f"markdown-memory-mcp: reading deprecated config file {LEGACY_CONFIG_NAME}; "
            f"rename it to {CONFIG_NAME}",
            file=sys.stderr,
        )
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
    elif config_path:
        raise ConfigError(f"config file not found: {path}")

    try:
        values = _validate(data)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    return Config(vault=root, **values)


def _validate(data: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(data) - _KEYS)
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(unknown)}")

    values: dict[str, Any] = {}
    for key in ("index_files", "exclude", "lint_exclude", "required_fields"):
        parsed = _str_list(data, key)
        if parsed is not None:
            values[key] = parsed
    if not values.get("index_files", ("x",)):
        raise ConfigError("index_files must not be empty")
    if "model" in data:
        if not isinstance(data["model"], str) or not data["model"].strip():
            raise ConfigError("model must be a non-empty string")
        values["model"] = data["model"].strip()
    for key, kind, lo, hi in (
        ("k", int, 1, 50),
        ("min_similarity", float, -1.0, 1.0),
        ("relative_margin", float, 0.0, 2.0),
        ("strong_similarity", float, -1.0, 1.0),
        ("max_index_line", int, 20, None),
        ("hook_max_updates", int, 0, None),
        ("duplicate_similarity", float, 0.0, 1.0),
    ):
        parsed = _number(data, key, kind, lo, hi)
        if parsed is not None:
            values[key] = parsed
    if "index_budget_bytes" in data:
        budget = data["index_budget_bytes"]
        if not isinstance(budget, dict) or set(budget) - {"soft", "hard"}:
            raise ConfigError("index_budget_bytes must be a table with keys soft and hard")
        soft = _number(budget, "soft", int, 1)
        hard = _number(budget, "hard", int, 1)
        values["budget_soft"] = soft if soft is not None else Config.budget_soft
        values["budget_hard"] = hard if hard is not None else Config.budget_hard
        if values["budget_soft"] > values["budget_hard"]:
            raise ConfigError("index_budget_bytes.soft must not exceed hard")
    return values
