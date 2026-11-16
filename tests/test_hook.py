from __future__ import annotations

import io
import json
import os
import time

import pytest

from conftest import write_note
from vault_recall import hook
from vault_recall.config import load_config
from vault_recall.embed import HashingEmbedder
from vault_recall.store import Store, cache_root
from vault_recall.vault import load_vault

PROMPT = "how do I keep the sourdough starter alive"


@pytest.fixture
def lenient(vault_dir):
    """The hashing embedder produces low cosines, so the threshold is lowered for the tests."""
    (vault_dir / ".vault-recall.toml").write_text("min_similarity = 0.1\n")
    return load_config(str(vault_dir))


@pytest.fixture
def indexed(lenient, embedder):
    Store(lenient.vault, lenient.model).update(load_vault(lenient).notes, embedder)
    return lenient


def call(cfg, prompt=PROMPT, session="s1", embedder=None, **extra):
    payload = {"prompt": prompt, "session_id": session, "cwd": "/path/to/project", **extra}
    factory = (lambda: embedder or HashingEmbedder()) if embedder is not False else None
    return hook.handle(payload, cfg, factory)


def context(out):
    data = json.loads(out)
    assert set(data) == {"hookSpecificOutput"}
    assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    return data["hookSpecificOutput"]["additionalContext"]


# --- skip rules


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "ok",
        "yes please",
        "thanks a lot",
        "ok thank you",
        "go ahead",
        "/compact now please",
        "  /verify the build output  ",
        "two words",
        "tamam sağol",
    ],
)
def test_short_acknowledgements_and_commands_are_skipped(prompt):
    assert hook.should_skip(prompt)


@pytest.mark.parametrize(
    "prompt", [PROMPT, "yes but what about the cache headers", "fix the nginx config please"]
)
def test_real_prompts_are_not_skipped(prompt):
    assert not hook.should_skip(prompt)


def test_skipped_prompt_prints_nothing(indexed):
    assert call(indexed, "ok") is None
    assert call(indexed, "/compact") is None


def test_no_index_yet_means_no_output_and_no_index_is_built(lenient, embedder):
    assert call(lenient) is None
    assert not Store(lenient.vault, lenient.model).exists()


def test_bm25_only_mode_works_without_embeddings(lenient):
    out = call(lenient, embedder=False)
    assert "sourdough-starter-feeding" in context(out)


# --- output


def test_output_format(indexed):
    text = context(call(indexed))
    lines = text.split("\n")
    assert lines[0] == hook.HEADER
    first = lines[1]
    assert first.startswith("- sourdough-starter-feeding — How often to feed a sourdough")
    assert first.endswith(f"({indexed.vault / 'sourdough-starter-feeding.md'})")
    assert len(lines) - 1 <= indexed.k


def test_notes_linked_from_the_index_are_excluded(indexed):
    # backup-before-migrations is listed in MEMORY.md, so it is already in context.
    # Its unlisted near-duplicate is a fair candidate.
    text = context(call(indexed, "always snapshot the database before running schema migrations"))
    assert "/backup-before-migrations.md)" not in text
    assert "/backup-before-migrations-old.md)" in text


def test_threshold_filters_weak_matches(indexed, vault_dir):
    (vault_dir / ".vault-recall.toml").write_text("min_similarity = 0.99\n")
    assert call(load_config(str(vault_dir))) is None


def test_hybrid_mode_requires_a_vector(indexed, vault_dir):
    write_note(
        vault_dir,
        "quokka-habitat.md",
        "---\nname: quokka-habitat\ndescription: d\n---\nquokka habitat",
    )
    # With the update budget at zero the new note stays unindexed and must not be injected.
    (vault_dir / ".vault-recall.toml").write_text("min_similarity = 0.0\nhook_max_updates = 0\n")
    out = call(load_config(str(vault_dir)), "tell me about the quokka habitat please")
    assert out is None or "quokka-habitat" not in context(out)


def test_small_changes_are_indexed_on_the_fly(indexed, vault_dir):
    write_note(
        vault_dir,
        "quokka-habitat.md",
        "---\nname: quokka-habitat\ndescription: where quokkas live\n---\nquokka habitat quokka",
    )
    out = call(indexed, "tell me about the quokka habitat please")
    assert "quokka-habitat" in context(out)


# --- session dedupe


def test_a_note_is_injected_once_per_session(indexed):
    assert "sourdough-starter-feeding" in context(call(indexed, session="a"))
    second = call(indexed, session="a")
    assert second is None or "sourdough-starter-feeding" not in context(second)
    assert "sourdough-starter-feeding" in context(call(indexed, session="b"))


def test_without_a_session_id_nothing_is_remembered(indexed):
    assert call(indexed, session=None)
    assert call(indexed, session=None)


def test_old_session_files_are_pruned(indexed):
    store = Store(indexed.vault, indexed.model)
    call(indexed, session="fresh")
    sessions = store.dir / "sessions"
    stale = sessions / "stale.json"
    stale.write_text("{}")
    old = time.time() - hook.SESSION_TTL_SECONDS - 60
    os.utime(stale, (old, old))
    call(indexed, session="another")
    assert not stale.exists()
    assert len(list(sessions.glob("*.json"))) == 2


# --- never blocks a prompt


def run(vault, stdin_text, factory=None):
    out = io.StringIO()
    code = hook.run(str(vault), None, io.StringIO(stdin_text), out, factory)
    return code, out.getvalue()


@pytest.mark.parametrize(
    "stdin_text",
    ["", "not json", "[]", "null", '{"prompt": 5}', '{"prompt": null}', "{", "\x00\xff"],
)
def test_garbage_stdin_is_silent(vault_dir, stdin_text):
    code, out = run(vault_dir, stdin_text)
    assert (code, out) == (0, "")


def test_failures_are_logged_and_silent(vault_dir, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(hook, "handle", boom)
    code, out = run(vault_dir, json.dumps({"prompt": PROMPT}))
    assert (code, out) == (0, "")
    log = (cache_root() / "hook.log").read_text()
    assert "RuntimeError: model exploded" in log and log.count("\n") == 1


def test_broken_vault_path_is_silent(tmp_path):
    code, out = run(tmp_path / "does-not-exist", json.dumps({"prompt": PROMPT}))
    assert (code, out) == (0, "")
    assert "not a directory" in (cache_root() / "hook.log").read_text()


def test_unloadable_model_is_silent(indexed):
    from vault_recall.embed import EmbedUnavailable

    def factory():
        raise EmbedUnavailable("model not downloaded")

    code, out = run(indexed.vault, json.dumps({"prompt": PROMPT}), factory)
    assert (code, out) == (0, "")
    assert "model not downloaded" in (cache_root() / "hook.log").read_text()


def test_log_is_trimmed(monkeypatch):
    monkeypatch.setattr(hook, "LOG_MAX_BYTES", 200)
    for i in range(40):
        hook.log_failure(RuntimeError(f"failure {i}"))
    assert (cache_root() / "hook.log").read_text().count("\n") <= 101


def test_hook_prints_json_through_run(indexed):
    code, out = run(
        indexed.vault, json.dumps({"prompt": PROMPT, "session_id": "x"}), lambda: HashingEmbedder()
    )
    assert code == 0
    assert "sourdough-starter-feeding" in context(out)
    assert out.endswith("\n") and out.count("\n") == 1


def test_log_failure_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    hook.log_failure(RuntimeError("x"), root=blocker / "sub")


def test_nothing_is_injected_when_the_best_match_is_already_in_context(indexed):
    # The nginx note is linked from MEMORY.md, so it is skipped. The notes below it are far
    # weaker, and must not be injected as filler.
    prompt = "cache control headers for static assets served by nginx"
    assert call(indexed, prompt) is None


def test_relative_margin_decides_how_close_a_runner_up_must_be(indexed, vault_dir):
    prompt = "cache control headers for static assets served by nginx"
    (vault_dir / ".vault-recall.toml").write_text("min_similarity = 0.0\nrelative_margin = 2.0\n")
    wide = call(load_config(str(vault_dir)), prompt)
    assert wide is not None and "nginx-cache-headers.md)" not in context(wide)


def test_runner_up_within_the_margin_is_kept(indexed):
    # The unlisted near-duplicate is about as close as the listed note it copies.
    prompt = "take a database snapshot and verify it restores before running schema migrations"
    assert "backup-before-migrations-old" in context(call(indexed, prompt))


def test_embedding_only_matches_must_be_strong(indexed, vault_dir):
    # The query shares no word with the vault, so bm25 is 0 for every note and the cosine
    # alone decides. Probe the real cosines first, then place the threshold around one.
    from vault_recall.search import build_corpus

    prompt = "zzzzqq wwwwxx yyyyvv"
    corpus = build_corpus(indexed, lambda: HashingEmbedder())
    listed = {"backup-before-migrations", "static-site-deploy", "home-lab-dns"}
    listed |= {"nginx-cache-headers", "bread-baking-schedule"}  # linked from MEMORY.md
    top = next(h for h in corpus.search(prompt, k=None) if h.note.stem not in listed)
    assert top.bm25 == 0 and top.cosine is not None

    def configure(strong):
        (vault_dir / ".vault-recall.toml").write_text(
            f"min_similarity = -1.0\nrelative_margin = 2.0\nstrong_similarity = {strong}\nk = 50\n"
        )
        return load_config(str(vault_dir))

    dropped = call(configure(top.cosine + 0.01), prompt)
    assert dropped is None or str(top.note.path) not in context(dropped)
    kept = call(configure(top.cosine - 0.01), prompt, session="other")
    assert str(top.note.path) in context(kept)
