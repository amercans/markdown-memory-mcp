from __future__ import annotations

import io
import json
import subprocess
import sys

import pytest

from vault_recall import __version__, cli, hook
from vault_recall.embed import HashingEmbedder
from vault_recall.store import Store


@pytest.fixture
def fake_model(monkeypatch):
    factory = lambda *args, **kwargs: lambda: HashingEmbedder()  # noqa: E731
    monkeypatch.setattr(cli, "default_factory", factory)
    monkeypatch.setattr(hook, "default_factory", factory)


@pytest.fixture
def no_embeddings(monkeypatch):
    monkeypatch.setattr(cli, "default_factory", lambda *args, **kwargs: None)
    monkeypatch.setattr(hook, "default_factory", lambda *args, **kwargs: None)


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == f"vault-recall {__version__}"


def test_a_command_is_required(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_index_builds_then_reports_up_to_date(vault_dir, fake_model, capsys):
    assert cli.main(["index", "--vault", str(vault_dir)]) == 0
    first = capsys.readouterr().out
    assert "13 added" in first and "0 updated" in first
    assert cli.main(["index", "--vault", str(vault_dir)]) == 0
    assert "up to date" in capsys.readouterr().out


def test_index_reports_incremental_changes(vault_dir, fake_model, capsys):
    cli.main(["index", "--vault", str(vault_dir)])
    capsys.readouterr()
    (vault_dir / "old-router-firmware.md").unlink()
    cli.main(["index", "--vault", str(vault_dir)])
    assert "1 removed" in capsys.readouterr().out


def test_index_without_the_extra_explains_how_to_install(vault_dir, no_embeddings, capsys):
    assert cli.main(["index", "--vault", str(vault_dir)]) == 1
    assert "vault-recall[all]" in capsys.readouterr().err


def test_search_text_output(vault_dir, fake_model, capsys):
    cli.main(["index", "--vault", str(vault_dir)])
    capsys.readouterr()
    assert cli.main(["search", "feed the sourdough starter", "--vault", str(vault_dir)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("1. sourdough-starter-feeding  [rrf ")
    assert "sourdough-starter-feeding.md" in out


def test_search_json_and_k(vault_dir, fake_model, capsys):
    cli.main(["index", "--vault", str(vault_dir)])
    capsys.readouterr()
    cli.main(["search", "sourdough", "-k", "2", "--json", "--vault", str(vault_dir)])
    hits = json.loads(capsys.readouterr().out)
    assert len(hits) == 2
    assert set(hits[0]) == {"name", "path", "description", "rrf", "cosine", "bm25"}
    assert hits[0]["cosine"] is not None


def test_search_falls_back_to_bm25_and_says_so(vault_dir, no_embeddings, capsys):
    assert cli.main(["search", "nginx cache", "--vault", str(vault_dir), "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["name"] == "nginx-cache-headers"
    assert json.loads(captured.out)[0]["cosine"] is None
    assert "BM25 only" in captured.err


def test_search_picks_up_new_notes_without_a_reindex(vault_dir, fake_model, capsys):
    cli.main(["index", "--vault", str(vault_dir)])
    (vault_dir / "late.md").write_text("---\nname: late\n---\nfresh")
    capsys.readouterr()
    # search updates the index itself, so nothing is stale afterwards
    cli.main(["search", "fresh", "--vault", str(vault_dir)])
    assert "not indexed" not in capsys.readouterr().err


def test_search_without_matches(vault_dir, no_embeddings, capsys):
    cli.main(["search", "zzzzzz", "--vault", str(vault_dir)])
    assert "No matches." in capsys.readouterr().out


def test_lint_notes_when_near_duplicate_check_is_unavailable(vault_dir, no_embeddings, capsys):
    cli.main(["lint", "--vault", str(vault_dir), "--fail-on", "never"])
    assert "VR008 skipped" in capsys.readouterr().err


def test_lint_runs_vr008_after_indexing(vault_dir, fake_model, monkeypatch, capsys):
    cli.main(["index", "--vault", str(vault_dir)])
    capsys.readouterr()
    monkeypatch.setattr("vault_recall.lint.embeddings_installed", lambda: True)
    cli.main(["lint", "--vault", str(vault_dir), "--fail-on", "never"])
    assert "VR008" in capsys.readouterr().out


def test_hook_command_reads_stdin(vault_dir, fake_model, monkeypatch, capsys):
    (vault_dir / ".vault-recall.toml").write_text("min_similarity = 0.1\n")
    cli.main(["index", "--vault", str(vault_dir)])
    capsys.readouterr()
    payload = json.dumps({"prompt": "how do I keep the sourdough starter alive", "session_id": "q"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert cli.main(["hook", "--vault", str(vault_dir)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "sourdough-starter-feeding" in out["hookSpecificOutput"]["additionalContext"]


def test_hook_command_exits_zero_on_a_bad_vault(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert cli.main(["hook", "--vault", str(tmp_path / "missing")]) == 0
    assert capsys.readouterr().out == ""


def test_errors_are_friendly(tmp_path, capsys):
    assert cli.main(["lint", "--vault", str(tmp_path / "missing")]) == 2
    assert capsys.readouterr().err.startswith("vault-recall: vault is not a directory")


def test_bad_config_is_reported(vault_dir, capsys):
    (vault_dir / ".vault-recall.toml").write_text("k = 0\n")
    assert cli.main(["search", "x", "--vault", str(vault_dir)]) == 2
    assert "k must be" in capsys.readouterr().err


def test_vault_from_environment(vault_dir, no_embeddings, monkeypatch, capsys):
    monkeypatch.setenv("VAULT_RECALL_VAULT", str(vault_dir))
    assert cli.main(["search", "nginx"]) == 0
    assert "nginx-cache-headers" in capsys.readouterr().out


def test_missing_mcp_extra_is_explained(vault_dir, no_embeddings, monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "mcp.server", None)
    monkeypatch.setitem(sys.modules, "mcp.server.fastmcp", None)
    assert cli.main(["serve", "--vault", str(vault_dir)]) == 2
    assert "vault-recall[all]" in capsys.readouterr().err


def test_index_leaves_the_vault_untouched(vault_dir, fake_model):
    before = sorted(p.name for p in vault_dir.rglob("*"))
    cli.main(["index", "--vault", str(vault_dir)])
    assert sorted(p.name for p in vault_dir.rglob("*")) == before
    assert Store(vault_dir, cli.load_config(str(vault_dir)).model).exists()


def test_module_entry_point(vault_dir):
    proc = subprocess.run(
        [sys.executable, "-m", "vault_recall.cli", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert proc.stdout.startswith("vault-recall ")


def test_search_never_downloads_the_model(vault_dir, fake_model, monkeypatch):
    cli.main(["index", "--vault", str(vault_dir)])
    seen = {}

    def spy(model, cache_root, *, local_only):
        seen["local_only"] = local_only
        return lambda: HashingEmbedder()

    monkeypatch.setattr(cli, "default_factory", spy)
    cli.main(["search", "nginx", "--vault", str(vault_dir)])
    assert seen == {"local_only": True}


def test_search_falls_back_when_the_model_is_missing(vault_dir, fake_model, monkeypatch, capsys):
    from vault_recall.embed import EmbedUnavailable

    cli.main(["index", "--vault", str(vault_dir)])
    capsys.readouterr()

    def missing():
        raise EmbedUnavailable("not downloaded")

    monkeypatch.setattr(cli, "default_factory", lambda *a, **k: missing)
    assert cli.main(["search", "nginx cache", "--vault", str(vault_dir), "--json"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["name"] == "nginx-cache-headers"
    assert "not downloaded" in captured.err and "BM25 only" in captured.err
    assert "downloads the model" in captured.err
