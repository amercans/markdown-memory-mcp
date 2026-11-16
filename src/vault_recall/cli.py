"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__, hook
from .config import Config, ConfigError, load_config
from .embed import INSTALL_HINT, EmbedderFactory, EmbedUnavailable, default_factory
from .lint import exit_code, format_human, lint, stored_vectors, summarise
from .search import build_corpus
from .store import Store, StoreError, cache_root
from .vault import load_vault


def _common() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", help="vault directory (default: $VAULT_RECALL_VAULT or cwd)")
    common.add_argument("--config", help="config file (default: <vault>/.vault-recall.toml)")
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vault-recall",
        description="Recall, lint and serve a folder of Markdown notes used as AI memory.",
    )
    parser.add_argument("--version", action="version", version=f"vault-recall {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    common = _common()

    sub.add_parser("index", parents=[common], help="build or update the embedding index")

    search = sub.add_parser("search", parents=[common], help="search the vault")
    search.add_argument("query")
    search.add_argument("-k", type=int, help="number of results (default: config k)")
    search.add_argument("--json", action="store_true", help="print results as JSON")

    lint_cmd = sub.add_parser("lint", parents=[common], help="check the vault for problems")
    lint_cmd.add_argument("--json", action="store_true", help="print findings as JSON")
    lint_cmd.add_argument(
        "--fail-on",
        choices=["error", "warning", "never"],
        default="error",
        help="lowest level that makes the exit code non-zero (default: error)",
    )

    sub.add_parser("hook", parents=[common], help="Claude Code UserPromptSubmit hook (stdin)")

    serve = sub.add_parser("serve", parents=[common], help="run the MCP server on stdio")
    serve.add_argument("--allow-write", action="store_true", help="also expose the write_note tool")
    return parser


def _factory(cfg: Config, *, local_only: bool = False) -> EmbedderFactory | None:
    return default_factory(cfg.model, cache_root(), local_only=local_only)


def cmd_index(cfg: Config) -> int:
    factory = _factory(cfg)
    if factory is None:
        print(f"embeddings are not installed: {INSTALL_HINT}", file=sys.stderr)
        print("search and lint still work without an index (BM25 only).", file=sys.stderr)
        return 1
    vault = load_vault(cfg)
    store = Store(cfg.vault, cfg.model)
    changes = store.changes(vault.notes)
    if not changes.count and store.exists():
        print(f"{len(vault.notes)} notes, index is up to date ({store.dir})")
        return 0
    result = store.update(vault.notes, factory() if changes.to_embed else None)
    print(
        f"{len(vault.notes)} notes: {result.added} added, {result.modified} updated, "
        f"{result.removed} removed, {result.unchanged} unchanged ({store.dir})"
    )
    return 0


def cmd_search(cfg: Config, args: argparse.Namespace) -> int:
    # Searching never downloads the model; only `index` does.
    corpus = build_corpus(cfg, _factory(cfg, local_only=True))
    if corpus is None:  # not reachable without require_index; keeps the type honest
        return 2
    if corpus.notice:
        print(
            f"embedding model not available ({corpus.notice}): ranking by BM25 only. "
            "`vault-recall index` downloads the model.",
            file=sys.stderr,
        )
    elif not corpus.hybrid:
        print(
            "no embedding index: ranking by BM25 only (run `vault-recall index`)", file=sys.stderr
        )
    elif corpus.stale:
        print(
            f"{corpus.stale} notes are not indexed yet (run `vault-recall index`)", file=sys.stderr
        )
    hits = corpus.search(args.query, k=args.k or cfg.k)
    if args.json:
        print(json.dumps([h.to_dict() for h in hits], indent=2, ensure_ascii=False))
        return 0
    if not hits:
        print("No matches.")
    for n, hit in enumerate(hits, start=1):
        cos = "-" if hit.cosine is None else f"{hit.cosine:.2f}"
        print(f"{n}. {hit.note.label}  [rrf {hit.rrf:.4f}  cosine {cos}  bm25 {hit.bm25:.2f}]")
        print(f"   {hit.note.rel}")
        if hit.note.description:
            print(f"   {hit.note.description}")
    return 0


def cmd_lint(cfg: Config, args: argparse.Namespace) -> int:
    vault = load_vault(cfg)
    vectors = stored_vectors(cfg, vault)
    findings = lint(cfg, vault, vectors)
    if args.json:
        payload = {
            "vault": str(cfg.vault),
            "summary": summarise(findings),
            "near_duplicate_check": vectors is not None,
            "findings": [f.to_dict() for f in findings],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(format_human(findings))
        if vectors is None:
            print(
                "VR008 skipped: needs the embedding index (`vault-recall index`)", file=sys.stderr
            )
    return exit_code(findings, args.fail_on)


def cmd_serve(cfg: Config, args: argparse.Namespace) -> int:
    from .mcp_server import VaultTools, build_server

    tools = VaultTools(cfg, _factory(cfg, local_only=True), args.allow_write)
    build_server(tools).run()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "hook":
        return hook.run(args.vault, args.config)
    try:
        cfg = load_config(args.vault, args.config)
        if args.command == "index":
            return cmd_index(cfg)
        if args.command == "search":
            return cmd_search(cfg, args)
        if args.command == "lint":
            return cmd_lint(cfg, args)
        return cmd_serve(cfg, args)
    except (ConfigError, StoreError, EmbedUnavailable) as exc:
        print(f"vault-recall: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"vault-recall: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
