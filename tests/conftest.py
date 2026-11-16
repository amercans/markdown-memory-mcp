from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from markdown_memory_mcp.config import Config, load_config
from markdown_memory_mcp.embed import HashingEmbedder

FIXTURE = Path(__file__).parent / "fixtures" / "vault"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKDOWN_MEMORY_CACHE", str(tmp_path / "cache"))
    monkeypatch.delenv("MARKDOWN_MEMORY_VAULT", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)


@pytest.fixture
def vault_dir(tmp_path) -> Path:
    """A private, mutable copy of the fixture vault."""
    target = tmp_path / "vault"
    shutil.copytree(FIXTURE, target)
    return target


@pytest.fixture
def cfg(vault_dir) -> Config:
    return load_config(str(vault_dir))


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


def write_note(vault: Path, name: str, text: str) -> Path:
    path = vault / name
    path.write_text(text, encoding="utf-8")
    return path
