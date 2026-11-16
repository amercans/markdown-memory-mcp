from __future__ import annotations

import os

from conftest import write_note
from vault_recall.config import load_config
from vault_recall.vault import (
    Link,
    Resolver,
    backticked_paths,
    extract_links,
    is_excluded,
    load_vault,
    mask_code,
    parse_frontmatter,
    parse_note,
)

# --- frontmatter


def test_frontmatter_basic():
    meta, body, status, line = parse_frontmatter("---\nname: a\ndescription: b\n---\n\nText\n")
    assert (meta, status) == ({"name": "a", "description": "b"}, "ok")
    assert body.strip() == "Text"
    assert line == 5


def test_frontmatter_bom_and_crlf():
    meta, body, status, _ = parse_frontmatter("﻿---\r\nname: a\r\n---\r\nBody\r\n")
    assert status == "ok" and meta == {"name": "a"}
    assert "\r" not in body


def test_no_frontmatter():
    meta, body, status, line = parse_frontmatter("# Title\n\ntext")
    assert (meta, status, line) == ({}, "none", 1)
    assert body.startswith("# Title")


def test_unterminated_frontmatter_is_treated_as_body():
    _, body, status, _ = parse_frontmatter("---\nname: a\nstill going")
    assert status == "none"
    assert "still going" in body


def test_broken_yaml_is_reported_not_raised():
    meta, body, status, _ = parse_frontmatter("---\nname: [unclosed\n---\nBody")
    assert (meta, status) == ({}, "broken")
    assert body == "Body"


def test_frontmatter_that_is_not_a_mapping_is_broken():
    assert parse_frontmatter("---\n- a\n- b\n---\nx")[2] == "broken"


def test_empty_frontmatter_block_is_an_empty_mapping():
    assert parse_frontmatter("---\n---\nx")[:3] == ({}, "x", "ok")


def test_empty_file(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("")
    note = parse_note(path, tmp_path)
    assert note is not None and note.frontmatter == "none" and note.body == ""


def test_non_string_values_are_coerced(tmp_path):
    path = write_note(tmp_path, "n.md", "---\nname: 2024\ndescription: true\n---\n")
    note = parse_note(path, tmp_path)
    assert note.name == "2024" and note.description == ""  # booleans are not text


def test_oversized_file_is_skipped(tmp_path):
    path = tmp_path / "big.md"
    path.write_bytes(b"x" * 1_000_001)
    assert parse_note(path, tmp_path) is None


def test_invalid_utf8_does_not_raise(tmp_path):
    path = tmp_path / "bad.md"
    path.write_bytes(b"---\nname: a\n---\ncaf\xe9")
    assert parse_note(path, tmp_path) is not None


# --- links


def note_from(tmp_path, body, name="n.md"):
    return parse_note(write_note(tmp_path, name, body), tmp_path)


def test_wikilink_alias_and_heading_are_stripped(tmp_path):
    note = note_from(tmp_path, "[[a|Alias]] [[b#Heading]] [[c#h|x]] [[d^block]] [[#local]]")
    assert [(link.kind, link.target) for link in extract_links(note)] == [
        ("wiki", "a"),
        ("wiki", "b"),
        ("wiki", "c"),
        ("wiki", "d"),
    ]


def test_links_in_code_are_ignored(tmp_path):
    body = "```\n[[fenced]]\n```\n~~~python\n[x](fenced.md)\n~~~\n`[[inline]]` [[real]]\n"
    assert [link.target for link in extract_links(note_from(tmp_path, body))] == ["real"]


def test_longer_closing_fence_and_nested_backticks(tmp_path):
    body = "````\n```\n[[inside]]\n```\n````\n[[after]]\n``a `[[tick]]` b``\n"
    assert [link.target for link in extract_links(note_from(tmp_path, body))] == ["after"]


def test_markdown_links(tmp_path):
    body = (
        "[t](a.md) [u](https://example.com/x.md) [m](mailto:a@example.com) [h](#top)\n"
        '[e](sub%20dir/b.md#sec) [q](c.md "Title") ![img](pic.png) [w](<d e.md>)\n'
    )
    targets = [(link.kind, link.target) for link in extract_links(note_from(tmp_path, body))]
    assert targets == [
        ("md", "a.md"),
        ("md", "sub dir/b.md"),
        ("md", "c.md"),
        ("md", "d e.md"),
    ]


def test_link_line_numbers_account_for_frontmatter(tmp_path):
    note = note_from(tmp_path, "---\nname: n\n---\n\nfirst\n[[x]]\n")
    assert extract_links(note) == [Link("wiki", "x", 6)]


def test_mask_code_keeps_length_and_lines():
    text = "a `code` b\n```\nblock\n```\nc"
    masked = mask_code(text)
    assert masked.count("\n") == text.count("\n")
    assert "code" not in masked and "block" not in masked


def test_backticked_paths(tmp_path):
    body = (
        "`~/notes/x.sh` `/usr/local/bin/tool` `/etc/hosts` `~/dir with space`\n"
        "`/api/v1/*` `not/a/path`\n"
        "```\n`/in/fence/ignored`\n```\n"
    )
    found = [p for _, p in backticked_paths(note_from(tmp_path, body))]
    assert found == ["~/notes/x.sh", "/usr/local/bin/tool", "/etc/hosts"]


def test_url_routes_are_not_paths(tmp_path):
    body = (
        "`/legal/cookies` `/blog/[slug]` `/assets/` `/verify` `/api/v1/users` `/docs/<id>`\n"
        "`/etc/{a,b}` `~/notes/[slug].md` `~/notes/*.md` `~/notes/\u2026`\n"
    )
    assert backticked_paths(note_from(tmp_path, body)) == []


def test_home_paths_are_always_checked(tmp_path):
    found = backticked_paths(note_from(tmp_path, "`~/no-such-dir/x`"))
    assert [p for _, p in found] == ["~/no-such-dir/x"]


# --- discovery


def test_load_vault_separates_indexes_and_excludes(vault_dir, cfg):
    (vault_dir / ".obsidian").mkdir()
    write_note(vault_dir / ".obsidian", "hidden.md", "x")
    (vault_dir / "sub").mkdir()
    write_note(vault_dir / "sub", "nested.md", "---\nname: nested\n---\n")
    vault = load_vault(cfg)
    assert [n.rel for n in vault.indexes] == ["MEMORY.md"]
    rels = {n.rel for n in vault.notes}
    assert "sub/nested.md" in rels
    assert ".obsidian/hidden.md" not in rels and "MEMORY.md" not in rels


def test_symlink_pointing_outside_is_skipped(vault_dir, tmp_path, cfg):
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    os.symlink(outside, vault_dir / "escape.md")
    inside_target = vault_dir / "old-router-firmware.md"
    os.symlink(inside_target, vault_dir / "alias.md")
    rels = {n.rel for n in load_vault(cfg).notes}
    assert "escape.md" not in rels
    assert "alias.md" in rels


def test_glob_matching():
    assert is_excluded(".obsidian/app.md", (".obsidian/**",))
    assert is_excluded("a/b/draft.md", ("**/draft.md",))
    assert is_excluded("draft.md", ("**/draft.md",))
    assert not is_excluded("a/drafts.md", ("**/draft.md",))
    assert is_excluded("archive/x.md", ("archive/*",))
    assert not is_excluded("archive/deep/x.md", ("archive/*",))


def test_custom_exclude_in_config(vault_dir):
    (vault_dir / ".vault-recall.toml").write_text('exclude = ["old-*.md"]\n')
    rels = {n.rel for n in load_vault(load_config(str(vault_dir))).notes}
    assert "old-router-firmware.md" not in rels


# --- resolution


def test_resolver_by_stem_and_frontmatter_name(cfg):
    resolver = Resolver(load_vault(cfg))
    assert resolver.lookup("name-mismatch").rel == "name-mismatch.md"
    assert resolver.lookup("network-diagram").rel == "name-mismatch.md"
    assert resolver.lookup("NETWORK-DIAGRAM").rel == "name-mismatch.md"
    assert resolver.lookup("nothing-here") is None


def test_inbound_links_include_index_files(cfg):
    vault = load_vault(cfg)
    inbound = Resolver(vault).inbound()
    by_rel = {note.path: note.rel for note in vault.notes}
    sources = {
        by_rel[path]: sorted(src.rel for src, _ in links)
        for path, links in inbound.items()
        if path in by_rel
    }
    assert "MEMORY.md" in sources["backup-before-migrations.md"]
    assert "old-router-firmware.md" not in sources
    assert "sourdough-starter-feeding.md" in sources


def test_non_note_files_resolve(vault_dir, cfg):
    (vault_dir / "diagram.png").write_bytes(b"x")
    write_note(vault_dir, "pics.md", "---\nname: pics\n---\n[[diagram.png]] [[missing.png]]\n")
    vault = load_vault(cfg)
    resolver = Resolver(vault)
    pics = next(n for n in vault.notes if n.stem == "pics")
    results = [resolver.resolve(link, pics) for link in extract_links(pics)]
    assert results == [True, None]


def test_elided_and_placeholder_paths_are_skipped(tmp_path):
    body = (
        "`~/.config/.../memory` `/Users/.../index.html` `~/notes/YYYY-MM-DD.md` "
        "`~/logs/XXX/app.log` `/etc/backup-MM_DD/x`\n"
        "`~/notes/MMM.md` `~/notes/real-DDay.md`\n"
    )
    found = [p for _, p in backticked_paths(note_from(tmp_path, body))]
    assert found == ["~/notes/MMM.md", "~/notes/real-DDay.md"]


def test_aliases_and_anchors_make_the_frontmatter_invalid():
    for text in ("a: &x 1\nb: *x", "a: &x [1]", "a: *nothing"):
        assert parse_frontmatter(f"---\n{text}\n---\nbody")[2] == "broken"


def test_name_and_description_accept_only_scalars_and_are_shortened(tmp_path):
    path = write_note(tmp_path, "n.md", "---\nname: [a, b]\ndescription: {x: 1}\n---\n")
    note = parse_note(path, tmp_path)
    assert note.name is None and note.description == ""
    path = write_note(tmp_path, "m.md", f"---\nname: {'n' * 400}\ndescription: {'d ' * 400}\n---\n")
    note = parse_note(path, tmp_path)
    assert len(note.name) == 300 and len(note.description) == 300


def test_dates_are_accepted_as_text(tmp_path):
    path = write_note(tmp_path, "d.md", "---\nname: 2026-11-16\n---\n")
    assert parse_note(path, tmp_path).name == "2026-11-16"
