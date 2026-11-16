"""Claude Code ``UserPromptSubmit`` hook.

Contract: never block a prompt. Any failure is logged to ``<cache>/hook.log`` and the hook
exits 0 without output. It also never downloads a model or builds an index from scratch.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from .config import Config, load_config
from .embed import EmbedderFactory, default_factory
from .search import build_corpus
from .store import Store, atomic_write, cache_root
from .text import fold, words
from .vault import Resolver

MIN_WORDS = 3
SESSION_TTL_SECONDS = 7 * 24 * 3600
LOG_MAX_BYTES = 64_000
_DEFAULT = object()
HEADER = "Possibly relevant notes from the memory vault (read before acting if relevant):"

_ACK_WORDS = frozenset(
    """
    ok okay k yes yep yeah no nope thanks thank you thx ty great good nice cool perfect sure
    fine right sounds looks lgtm go ahead continue proceed do it please done got it
    a lot much very so that works worked all
    evet hayir tamam peki tesekkurler sagol olur
    """.split()  # noqa: SIM905 - a word list reads better as prose
)


def is_acknowledgement(prompt: str) -> bool:
    tokens = words(prompt)
    return bool(tokens) and len(tokens) <= 6 and all(fold(t) in _ACK_WORDS for t in tokens)


def should_skip(prompt: str) -> bool:
    stripped = prompt.strip()
    return (
        stripped.startswith("/") or len(words(stripped)) < MIN_WORDS or is_acknowledgement(stripped)
    )


def _session_file(store: Store, session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    return store.dir / "sessions" / f"{digest}.json"


def _injected(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")).get("injected", []))
    except (OSError, ValueError, AttributeError):
        return set()


def _remember(path: Path, injected: set[str]) -> None:
    payload = json.dumps({"injected": sorted(injected)}).encode()
    atomic_write(path, lambda fh: fh.write(payload))
    cutoff = time.time() - SESSION_TTL_SECONDS
    for old in path.parent.glob("*.json"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            continue


def handle(payload: object, cfg: Config, factory: EmbedderFactory | None) -> str | None:
    """The JSON to print for this prompt, or ``None`` when nothing should be injected."""
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or should_skip(prompt):
        return None
    session_id = payload.get("session_id")

    store = Store(cfg.vault, cfg.model)
    corpus = build_corpus(
        cfg, factory, max_updates=cfg.hook_max_updates, require_index=True, store=store
    )
    if corpus is None:
        return None

    # Notes the index files link to are already in the assistant's context.
    notes_by_path = {n.path: n for n in corpus.notes}
    exclude = {
        notes_by_path[target].rel
        for target, sources in Resolver(corpus.vault).inbound().items()
        if target in notes_by_path and any(src.is_index for src, _ in sources)
    }
    state = _session_file(store, session_id) if isinstance(session_id, str) and session_id else None
    seen = _injected(state) if state else set()
    skip = exclude | seen

    # Rank over every note, so that a strong match that is skipped (already in context)
    # still sets the bar: weaker notes must be close to it, or nothing is injected.
    hits = corpus.search(prompt, k=None)
    cosines = [h.cosine for h in hits if h.cosine is not None]
    floor = cfg.min_similarity
    if cosines:
        floor = max(floor, max(cosines) - cfg.relative_margin)
    chosen = []
    for hit in hits:
        if hit.note.rel in skip:
            continue
        if hit.cosine is None:
            # No vector for this note. Lexical matches count only when there are no embeddings.
            if corpus.hybrid or hit.bm25 <= 0:
                continue
        elif hit.cosine < floor:
            continue
        chosen.append(hit)
        if len(chosen) == cfg.k:
            break
    if not chosen:
        return None

    lines = [HEADER]
    for hit in chosen:
        note = hit.note
        desc = f" — {note.description}" if note.description else ""
        lines.append(f"- {note.label}{desc} ({note.path})")
    if state:
        _remember(state, seen | {h.note.rel for h in chosen})
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "\n".join(lines),
            }
        },
        ensure_ascii=False,
    )


def log_failure(exc: BaseException, root: Path | None = None) -> None:
    """Append one line to ``hook.log``. Must not raise."""
    try:
        root = root or cache_root()
        root.mkdir(parents=True, exist_ok=True)
        log = root / "hook.log"
        if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
            tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-100:]
            log.write_text("\n".join(tail) + "\n", encoding="utf-8")
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = " ".join(f"{type(exc).__name__}: {exc}".split())[:300]
        with log.open("a", encoding="utf-8") as handle_:
            handle_.write(f"{stamp} {message}\n")
    except Exception:
        return


def run(
    vault: str | None,
    config: str | None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    factory: EmbedderFactory | object | None = _DEFAULT,
) -> int:
    """Entry point used by the CLI. Always returns 0."""
    try:
        payload = json.loads((stdin or sys.stdin).read())
        cfg = load_config(vault, config)
        if factory is _DEFAULT:
            factory = default_factory(cfg.model, cache_root(), local_only=True)
        out = handle(payload, cfg, factory)  # type: ignore[arg-type]
        if out:
            (stdout or sys.stdout).write(out + "\n")
    except Exception as exc:
        log_failure(exc)
    return 0
