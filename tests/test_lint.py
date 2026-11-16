from __future__ import annotations

import json

import pytest

from conftest import write_note
from vault_recall.cli import main
from vault_recall.config import ConfigError, load_config
from vault_recall.lint import Finding, exit_code, format_human, lint, stored_vectors, summarise
from vault_recall.store import Store
from vault_recall.vault import load_vault


def run_lint(cfg, embedder=None):
    vault = load_vault(cfg)
    vectors = None
    if embedder is not None:
        store = Store(cfg.vault, cfg.model)
        store.update(vault.notes, embedder)
        vectors = store.vectors_for(vault.notes)
    return lint(cfg, vault, vectors)


def by_code(findings):
    out: dict[str, list] = {}
    for f in findings:
        out.setdefault(f.code, []).append(f)
    return out


def test_every_code_fires_on_the_fixture_vault(cfg, embedder):
    codes = by_code(run_lint(cfg, embedder))
    assert sorted(codes) == [f"VR{n:03d}" for n in (1, 2, 3, 4, 5, 6, 8, 9, 10)]
    # VR007 needs an oversized index; it is covered separately below.


def test_levels_match_the_rule_table(cfg, embedder):
    codes = by_code(run_lint(cfg, embedder))
    expected = {
        "VR001": "error",
        "VR002": "error",
        "VR003": "error",
        "VR004": "warning",
        "VR005": "error",
        "VR006": "info",
        "VR008": "warning",
        "VR009": "warning",
        "VR010": "warning",
    }
    for code, level in expected.items():
        assert {f.level for f in codes[code]} == {level}, code


def test_vr001_broken_wikilink_ignores_code_and_resolves_by_name(cfg):
    messages = [f.message for f in by_code(run_lint(cfg))["VR001"]]
    assert messages == ["broken wikilink [[nginx-gzip-settings]]"]  # [[network-diagram]] resolves


def test_vr002_only_for_index_files(cfg, vault_dir):
    write_note(vault_dir, "plain.md", "---\nname: plain\ndescription: d\n---\n[gone](gone.md)\n")
    findings = by_code(run_lint(cfg))["VR002"]
    assert [(f.path, f.line) for f in findings] == [("MEMORY.md", 10)]


def test_vr003_variants(cfg, vault_dir):
    write_note(vault_dir, "bare.md", "just text\n")
    write_note(vault_dir, "broken.md", "---\nname: [oops\n---\ntext\n")
    write_note(vault_dir, "empty-desc.md", "---\nname: empty-desc\ndescription: ''\n---\n")
    got = {f.path: f.message for f in by_code(run_lint(cfg))["VR003"]}
    assert got["bare.md"] == "no frontmatter"
    assert "not a valid YAML mapping" in got["broken.md"]
    assert "'description'" in got["empty-desc.md"]
    assert "'description'" in got["missing-description.md"]


def test_required_fields_are_configurable(cfg, vault_dir):
    (vault_dir / ".vault-recall.toml").write_text('required_fields = ["name", "type"]\n')
    messages = [f.message for f in by_code(run_lint(load_config(str(vault_dir))))["VR003"]]
    assert any("'type'" in m for m in messages)


def test_vr004_and_vr005(cfg):
    codes = by_code(run_lint(cfg))
    assert [f.path for f in codes["VR004"] if "network" in f.message] == ["name-mismatch.md"]
    dup_paths = sorted(f.path for f in codes["VR005"])
    assert dup_paths == ["raspberry-pi-notes.md", "raspberry-pi-setup.md"]


def test_vr006_orphans(cfg):
    orphans = sorted(f.path for f in by_code(run_lint(cfg))["VR006"])
    assert orphans == ["backup-before-migrations-old.md", "old-router-firmware.md"]


def test_vr007_soft_and_hard_budget(cfg, vault_dir):
    size = (vault_dir / "MEMORY.md").stat().st_size
    (vault_dir / ".vault-recall.toml").write_text(
        f"[index_budget_bytes]\nsoft = {size - 10}\nhard = {size + 10}\n"
    )
    (finding,) = by_code(run_lint(load_config(str(vault_dir))))["VR007"]
    assert finding.level == "warning" and finding.path == "MEMORY.md"

    (vault_dir / ".vault-recall.toml").write_text(
        f"[index_budget_bytes]\nsoft = {size - 20}\nhard = {size - 10}\n"
    )
    (finding,) = by_code(run_lint(load_config(str(vault_dir))))["VR007"]
    assert finding.level == "error"


def test_vr008_needs_embeddings_and_names_the_pair(cfg, embedder):
    assert "VR008" not in by_code(run_lint(cfg))
    (finding,) = by_code(run_lint(cfg, embedder))["VR008"]
    assert finding.path == "backup-before-migrations-old.md"
    assert "backup-before-migrations.md" in finding.message


def test_vr008_threshold_is_configurable(cfg, embedder, vault_dir):
    (vault_dir / ".vault-recall.toml").write_text("duplicate_similarity = 0.999\n")
    assert "VR008" not in by_code(run_lint(load_config(str(vault_dir)), embedder))


def test_vr009_only_missing_paths(cfg):
    (finding,) = by_code(run_lint(cfg))["VR009"]
    assert finding.message.endswith("~/does-not-exist/tool.sh")
    assert finding.path == "nginx-cache-headers.md"  # /etc/hosts exists and is not reported


def test_vr010_reports_the_long_line(cfg):
    (finding,) = by_code(run_lint(cfg))["VR010"]
    assert (finding.path, finding.line) == ("MEMORY.md", 14)


def test_clean_vault_has_no_findings(tmp_path):
    vault = tmp_path / "clean"
    vault.mkdir()
    write_note(vault, "MEMORY.md", "# Index\n- [A](a.md) - first\n")
    write_note(vault, "a.md", "---\nname: a\ndescription: first note\n---\nSee [[b]].\n")
    write_note(vault, "b.md", "---\nname: b\ndescription: second note\n---\n[[a]]\n")
    cfg = load_config(str(vault))
    findings = lint(cfg, load_vault(cfg))
    assert findings == []


def test_findings_are_sorted_and_serialisable(cfg):
    findings = run_lint(cfg)
    keys = [(f.path, f.line or 0, f.code) for f in findings]
    assert keys == sorted(keys)
    assert set(findings[0].to_dict()) == {"code", "level", "path", "line", "message"}


def test_exit_code_policy(cfg):
    findings = run_lint(cfg)
    assert exit_code(findings, "error") == 1
    assert exit_code(findings, "warning") == 1
    assert exit_code(findings, "never") == 0
    warnings_only = [f for f in findings if f.level == "warning"]
    assert exit_code(warnings_only, "error") == 0
    assert exit_code(warnings_only, "warning") == 1
    assert exit_code([], "warning") == 0


def test_summary_and_human_format(cfg):
    findings = run_lint(cfg)
    counts = summarise(findings)
    assert counts["error"] == 5 and counts["info"] == 2
    text = format_human(findings)
    assert text.startswith("MEMORY.md\n")
    assert "VR001  error" in text and text.rstrip().endswith("5 errors, 4 warnings, 2 info")
    assert format_human([]) == "No problems found."


def test_stored_vectors_requires_an_index(cfg, embedder):
    vault = load_vault(cfg)
    assert stored_vectors(cfg, vault) is None
    Store(cfg.vault, cfg.model).update(vault.notes, embedder)
    assert stored_vectors(cfg, vault) is not None


# --- CLI behaviour


def test_cli_exit_codes(vault_dir, capsys):
    assert main(["lint", "--vault", str(vault_dir)]) == 1
    assert main(["lint", "--vault", str(vault_dir), "--fail-on", "never"]) == 0
    capsys.readouterr()


def test_cli_json_shape(vault_dir, capsys):
    main(["lint", "--vault", str(vault_dir), "--json", "--fail-on", "never"])
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"vault", "summary", "near_duplicate_check", "findings"}
    assert payload["summary"]["error"] == 5
    assert {"code", "level", "path", "line", "message"} == set(payload["findings"][0])


@pytest.mark.parametrize(("fail_on", "expected"), [("error", 0), ("warning", 1)])
def test_cli_fail_on_levels_with_only_warnings(tmp_path, fail_on, expected):
    vault = tmp_path / "warn"
    vault.mkdir()
    write_note(vault, "MEMORY.md", "# Index\n- [A](a.md) - first\n")
    write_note(vault, "a.md", "---\nname: other-name\ndescription: first\n---\n")
    assert main(["lint", "--vault", str(vault), "--fail-on", fail_on]) == expected


def test_vr009_skips_url_routes_but_checks_real_prefixes(cfg, vault_dir):
    write_note(
        vault_dir,
        "paths.md",
        "---\nname: paths\ndescription: d\n---\n[[home-lab-dns]]\n"
        "`/legal/cookies` `/blog/[slug]` `/assets/` `/etc/definitely-missing-file`\n",
    )
    messages = [f.message for f in by_code(run_lint(cfg))["VR009"] if f.path == "paths.md"]
    assert messages == ["path in code span does not exist: /etc/definitely-missing-file"]


def test_lint_exclude_hides_findings_but_keeps_files_searchable(vault_dir):
    (vault_dir / "archive").mkdir()
    write_note(vault_dir, "archive/old.md", "no frontmatter and [[nowhere]]\n")
    cfg = load_config(str(vault_dir))
    assert "archive/old.md" in {f.path for f in lint(cfg, load_vault(cfg))}

    (vault_dir / ".vault-recall.toml").write_text('lint_exclude = ["archive/**"]\n')
    cfg = load_config(str(vault_dir))
    vault = load_vault(cfg)
    assert not [f for f in lint(cfg, vault) if f.path.startswith("archive/")]
    assert "archive/old.md" in {n.rel for n in vault.notes}  # still part of the vault
    assert any(f.code == "VR001" for f in lint(cfg, vault))  # other files are still linted


def test_lint_exclude_is_validated(vault_dir):
    (vault_dir / ".vault-recall.toml").write_text('lint_exclude = "archive"\n')
    with pytest.raises(ConfigError):
        load_config(str(vault_dir))


def test_non_scalar_name_or_description_counts_as_missing(cfg, vault_dir):
    write_note(vault_dir, "listy.md", "---\nname: [a, b]\ndescription: {x: 1}\n---\n")
    messages = [f.message for f in by_code(run_lint(cfg))["VR003"] if f.path == "listy.md"]
    assert sorted(messages) == [
        "missing required field 'description'",
        "missing required field 'name'",
    ]


def test_alias_in_frontmatter_is_invalid_frontmatter(cfg, vault_dir):
    write_note(vault_dir, "aliased.md", "---\nname: &a aliased\ndescription: *a\n---\n")
    messages = [f.message for f in by_code(run_lint(cfg))["VR003"] if f.path == "aliased.md"]
    assert messages == ["frontmatter is not a valid YAML mapping"]


def test_summary_line_uses_singular_and_plural(cfg):
    one = [Finding("VR001", "error", "a.md", 1, "x")]
    assert format_human(one).endswith("1 error, 0 warnings, 0 info")
    mixed = [Finding("VR004", "warning", "a.md", 1, "x")] * 2 + [
        Finding("VR006", "info", "a.md", None, "x")
    ]
    assert format_human(mixed).endswith("0 errors, 2 warnings, 1 info")
