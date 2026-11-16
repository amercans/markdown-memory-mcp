from __future__ import annotations

import os

import pytest
import yaml

from vault_recall.config import load_config
from vault_recall.mcp_server import ToolError, VaultTools, build_server
from vault_recall.vault import parse_frontmatter


@pytest.fixture
def tools(cfg):
    return VaultTools(cfg, None, allow_write=False)


@pytest.fixture
def writable(cfg):
    return VaultTools(cfg, None, allow_write=True)


def registered(tools: VaultTools) -> set[str]:
    return {t.name for t in build_server(tools)._tool_manager.list_tools()}


# --- read tools


def test_search_notes_returns_ranked_dicts(tools):
    hits = tools.search_notes("sourdough starter feeding", k=2)
    assert len(hits) == 2
    assert hits[0]["name"] == "sourdough-starter-feeding"
    assert {"path", "description", "rrf", "bm25"} <= set(hits[0])


def test_search_notes_rejects_empty_query(tools):
    with pytest.raises(ToolError):
        tools.search_notes("   ")


def test_read_note_by_stem_and_by_frontmatter_name(tools):
    by_stem = tools.read_note("name-mismatch")
    by_name = tools.read_note("network-diagram")
    assert by_stem == by_name
    assert by_stem["path"] == "name-mismatch.md"
    assert by_stem["content"].startswith("---\nname: network-diagram")
    assert tools.read_note("name-mismatch.md")["path"] == "name-mismatch.md"


def test_read_note_can_read_the_index_file(tools):
    assert tools.read_note("MEMORY")["content"].startswith("# Memory Index")


def test_read_note_unknown(tools):
    with pytest.raises(ToolError, match="no note named"):
        tools.read_note("does-not-exist")


@pytest.mark.parametrize(
    "name",
    ["../secret", "..", "a/b", "/etc/passwd", "~/x", "sub\\file", "", "  ", "a\0b", "x..y"],
)
def test_path_like_names_are_refused(tools, name):
    with pytest.raises(ToolError):
        tools.read_note(name)


def test_symlink_escape_is_not_readable(tools, vault_dir, tmp_path):
    secret = tmp_path / "secret.md"
    secret.write_text("---\nname: secret\n---\ntop secret")
    os.symlink(secret, vault_dir / "leak.md")
    with pytest.raises(ToolError):
        tools.read_note("leak")
    with pytest.raises(ToolError):
        tools.read_note("secret")


def test_backlinks(tools):
    links = tools.backlinks("sourdough-starter-feeding")
    assert [item["path"] for item in links] == ["bread-baking-schedule.md"]
    assert links[0]["line"] > 1
    assert {item["path"] for item in tools.backlinks("backup-before-migrations")} == {"MEMORY.md"}
    assert tools.backlinks("old-router-firmware") == []


def test_backlinks_resolve_frontmatter_names(tools):
    assert [i["path"] for i in tools.backlinks("network-diagram")] == ["home-lab-dns.md"]


def test_lint_vault(tools):
    findings = tools.lint_vault()["findings"]
    assert {f["code"] for f in findings} >= {"VR001", "VR002", "VR003", "VR005"}


# --- write_note


def test_write_requires_the_flag(tools, vault_dir):
    with pytest.raises(ToolError, match="--allow-write"):
        tools.write_note("fresh-note", "d", "body")
    assert not (vault_dir / "fresh-note.md").exists()


def test_write_tool_is_only_registered_with_the_flag(tools, writable):
    assert registered(tools) == {"search_notes", "read_note", "backlinks", "lint_vault"}
    assert registered(writable) == {
        "search_notes",
        "read_note",
        "backlinks",
        "lint_vault",
        "write_note",
    }


def test_write_note_roundtrip(writable, vault_dir):
    result = writable.write_note(
        "kitchen-scale", "Calibrate with a 100 g weight", "Body text.\n", type="reference"
    )
    assert result == {"path": "kitchen-scale.md"}
    text = (vault_dir / "kitchen-scale.md").read_text()
    meta, body, status, _ = parse_frontmatter(text)
    assert status == "ok"
    assert meta == {
        "name": "kitchen-scale",
        "description": "Calibrate with a 100 g weight",
        "type": "reference",
    }
    assert body.strip() == "Body text."
    assert (vault_dir / "kitchen-scale.md").stat().st_mode & 0o777 == 0o644
    assert writable.read_note("kitchen-scale")["description"].startswith("Calibrate")


def test_overwrite_is_refused_unless_asked(writable, vault_dir):
    writable.write_note("scratch", "first", "one")
    with pytest.raises(ToolError, match="already exists"):
        writable.write_note("scratch", "second", "two")
    assert "one" in (vault_dir / "scratch.md").read_text()
    writable.write_note("scratch", "second", "two", overwrite=True)
    assert "two" in (vault_dir / "scratch.md").read_text()


def test_existing_fixture_note_is_protected(writable, vault_dir):
    before = (vault_dir / "home-lab-dns.md").read_text()
    with pytest.raises(ToolError):
        writable.write_note("home-lab-dns", "d", "replaced")
    assert (vault_dir / "home-lab-dns.md").read_text() == before


def test_name_clash_with_a_frontmatter_name_is_refused(writable):
    with pytest.raises(ToolError, match="already exists"):
        writable.write_note("network-diagram", "d", "b")


@pytest.mark.parametrize(
    "name",
    ["../escape", "a/b", "/abs", "UPPER", "has space", "-leading", "dot.md", "", "x" * 82, "ünï"],
)
def test_invalid_names_never_touch_the_disk(writable, vault_dir, tmp_path, name):
    before = sorted(p.name for p in vault_dir.glob("*"))
    with pytest.raises(ToolError):
        writable.write_note(name, "d", "b")
    assert sorted(p.name for p in vault_dir.glob("*")) == before
    assert not (tmp_path / "escape.md").exists()


def test_longest_valid_slug_is_accepted(writable):
    assert writable.write_note("a" * 81, "d", "b")["path"] == "a" * 81 + ".md"


def test_index_file_is_never_written(vault_dir):
    (vault_dir / ".vault-recall.toml").write_text('index_files = ["recall.md", "MEMORY.md"]\n')
    (vault_dir / "recall.md").write_text("# Index\n")
    tools = VaultTools(load_config(str(vault_dir)), None, allow_write=True)
    before = (vault_dir / "recall.md").read_bytes()
    with pytest.raises(ToolError, match="index"):
        tools.write_note("recall", "d", "b", overwrite=True)
    assert (vault_dir / "recall.md").read_bytes() == before


def test_write_through_a_symlink_is_refused(writable, vault_dir, tmp_path):
    victim = tmp_path / "victim.md"
    victim.write_text("original")
    os.symlink(victim, vault_dir / "linked.md")
    with pytest.raises(ToolError, match="symlink"):
        writable.write_note("linked", "d", "b", overwrite=True)
    assert victim.read_text() == "original"


def test_write_validates_description_type_and_size(writable):
    with pytest.raises(ToolError):
        writable.write_note("ok-name", "   ", "b")
    with pytest.raises(ToolError):
        writable.write_note("ok-name", "x" * 501, "b")
    with pytest.raises(ToolError):
        writable.write_note("ok-name", "d", "b", type="bad type!")
    with pytest.raises(ToolError, match="too large"):
        writable.write_note("ok-name", "d", "x" * 600_000)


def test_multiline_description_is_collapsed_and_yaml_is_safe(writable, vault_dir):
    writable.write_note("tricky", "line one\nline two: --- # not a comment", "b")
    text = (vault_dir / "tricky.md").read_text()
    assert parse_frontmatter(text)[0]["description"] == "line one line two: --- # not a comment"
    assert yaml.safe_load(text.split("---\n")[1])["name"] == "tricky"


def test_no_temp_files_remain(writable, vault_dir):
    writable.write_note("tidy", "d", "b")
    assert not [p for p in vault_dir.glob("*") if p.name.startswith(".write-note-")]


@pytest.mark.parametrize("create_index", [True, False])
def test_index_name_in_another_case_is_refused(vault_dir, create_index):
    # "memory" is the same file as MEMORY.md on a case-insensitive file system.
    (vault_dir / "MEMORY.md").unlink()
    if create_index:
        (vault_dir / "MEMORY.md").write_text("# Index\n")
    tools = VaultTools(load_config(str(vault_dir)), None, allow_write=True)
    with pytest.raises(ToolError, match="index"):
        tools.write_note("memory", "d", "b", overwrite=True)
    assert [p.name for p in vault_dir.glob("*") if p.name.lower() == "memory.md"] == (
        ["MEMORY.md"] if create_index else []
    )


def test_every_tool_has_a_description_with_its_arguments(writable):
    tools = {t.name: t for t in build_server(writable)._tool_manager.list_tools()}
    assert set(tools) == {"search_notes", "read_note", "backlinks", "lint_vault", "write_note"}
    for tool in tools.values():
        assert len(tool.description) > 60, tool.name
    for name in ("query", "k"):
        assert name in tools["search_notes"].description
    for name in ("description", "body", "overwrite"):
        assert name in tools["write_note"].description
