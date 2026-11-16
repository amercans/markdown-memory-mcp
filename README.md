# markdown-memory-mcp

[![CI](https://github.com/amercans/markdown-memory-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/amercans/markdown-memory-mcp/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/amercans/markdown-memory-mcp/badge)](https://scorecard.dev/viewer/?uri=github.com/amercans/markdown-memory-mcp)
[![Release](https://img.shields.io/github/v/release/amercans/markdown-memory-mcp)](https://github.com/amercans/markdown-memory-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Semantic recall for a Markdown memory vault: a Claude Code hook that brings back the right notes, a linter that keeps the vault healthy, and an MCP server for any MCP client. Local and multilingual.

It works with Obsidian-style vaults: a folder of notes with YAML frontmatter, `[[wikilinks]]`, and an index file such as `MEMORY.md` that links to the notes.

## What you get

- **Recall hook.** A [Claude Code](https://docs.claude.com/en/docs/claude-code) `UserPromptSubmit` hook that adds the notes relevant to each prompt, without the index file having to list them.
- **Search CLI.** `markdown-memory-mcp search` ranks notes by meaning and by words (BM25 plus embeddings) and shows the score breakdown.
- **Vault linter.** `markdown-memory-mcp lint` checks links, frontmatter, duplicates, orphans and index size, with rules VR001 to VR010.
- **MCP server.** `markdown-memory-mcp serve` exposes search, read, backlinks and lint to any MCP client, and optionally a write tool.

The files stay the source of truth. markdown-memory-mcp never writes to your vault. The only exception is the MCP `write_note` tool, which exists only if you start the server with `--allow-write`.

## Why

An index file that is loaded into every session has to stay small. As a vault grows, older notes get moved out of the index, and from then on the assistant finds them only when it happens to search for the exact words the note uses. "Keep the starter alive" does not match a note titled "sourdough starter feeding".

markdown-memory-mcp closes that gap in two ways:

- **Recall by meaning.** It ranks notes with BM25 and with embeddings and merges both rankings. The hook injects the few notes that match the current prompt and are not already in the index.
- **A healthier vault.** `markdown-memory-mcp lint` finds broken links, notes without a description, duplicate names, orphans, an index that outgrew its budget, and notes that are near-copies of each other.

## How it works

```
 prompt ──▶ UserPromptSubmit hook ──▶ markdown-memory-mcp hook
                                          │
   notes (*.md), read on every call ──────┤
        │                                 ▼
        │                     BM25 (folded, 5-letter stems) ──┐
        │                                                     ├─ RRF ─▶ filters ─▶ additionalContext
        └─▶ embeddings, cached outside the vault ── cosine ───┘

   filters: not linked from an index file, not injected earlier in this session,
            cosine >= min_similarity, at most k notes
```

- **Lexical side.** Text is Unicode-normalised, `İ` and `I` are folded to `i`, and every word is cut to its first five letters. This crude stemming works well for agglutinative languages such as Turkish. File names count twice, as a cheap title boost.
- **Semantic side.** [fastembed](https://github.com/qdrant/fastembed) runs a small model locally (ONNX, no GPU). The default model is multilingual. Queries in the same language as the note work best. Recall across languages, for example an English query for a Turkish note, is weaker. Turkish and other agglutinative text gets most of its help from the folding and prefix stems on the lexical side. Vectors are kept in a cache directory outside the vault and are updated incrementally.
- **Fusion.** Reciprocal Rank Fusion with k = 60 over the two rankings. `search --json` returns the score breakdown.
- **Without the `embed` extra** everything still works on BM25 alone, except the near-duplicate lint rule.

The design, invariants and threat model are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/) (pip works too).
- For embeddings: about 220 MB of disk for the default model, downloaded on the first `index`.

## Install

```sh
uv tool install "markdown-memory-mcp[all] @ git+https://github.com/amercans/markdown-memory-mcp"
```

Extras: `embed` (fastembed and numpy), `mcp` (the MCP server), `all` (both). The core install depends on PyYAML only.

## Quick start

The examples run on the small invented vault that ships in the repository. Clone it to follow along:

```sh
git clone https://github.com/amercans/markdown-memory-mcp
cd markdown-memory-mcp
```

For your own vault, use `--vault tests/fixtures/vault` instead of `--vault tests/fixtures/vault`. If you run a command from inside the vault, or set `MARKDOWN_MEMORY_VAULT`, `--vault` can be left out.

Build the index. The first run downloads the model (about 220 MB, from a public repository, so no Hugging Face token is needed) and shows a download progress bar. Later runs embed only new and changed notes. `search`, `lint`, the hook and the server never download anything.

```console
$ markdown-memory-mcp index --vault tests/fixtures/vault
13 notes: 13 added, 0 updated, 0 removed, 0 unchanged (~/.cache/markdown-memory-mcp/<hash>)
```

Search. Neither "keep" nor "alive" appears in the note that comes first:

```console
$ markdown-memory-mcp search "how do I keep the starter alive" --vault tests/fixtures/vault -k 1
1. sourdough-starter-feeding  [rrf 0.0328  cosine 0.47  bm25 7.23]
   sourdough-starter-feeding.md
   How often to feed a sourdough starter and how to revive one that has gone sluggish
```

A Turkish query finds the Turkish note. This hit comes from the lexical side (the stem `simit`); the cosine is low. It demonstrates folding and stemming, not cross-language recall:

```console
$ markdown-memory-mcp search "simit nasıl yapılır" --vault tests/fixtures/vault -k 1
1. istanbul-simit  [rrf 0.0328  cosine 0.13  bm25 3.23]
   istanbul-simit.md
   İstanbul usulü simit tarifi, pekmezli su ve susam ile
```

Lint. The fixture vault contains one deliberate defect for each rule, apart from VR007:

```console
$ markdown-memory-mcp lint --vault tests/fixtures/vault
MEMORY.md
  L10   VR002  error    index links to missing file retired-vpn-notes.md
  L14   VR010  warning  line has 244 characters (max 200)
backup-before-migrations-old.md
        VR006  info     no note or index links here
        VR008  warning  near-duplicate of backup-before-migrations.md (cosine 0.98)
missing-description.md
  L1    VR003  error    missing required field 'description'
name-mismatch.md
  L1    VR004  warning  name 'network-diagram' differs from file name
nginx-cache-headers.md
  L11   VR001  error    broken wikilink [[nginx-gzip-settings]]
  L13   VR009  warning  path in code span does not exist: ~/does-not-exist/tool.sh
old-router-firmware.md
        VR006  info     no note or index links here
raspberry-pi-notes.md
  L1    VR004  warning  name 'raspberry-pi-setup' differs from file name
  L1    VR005  error    name 'raspberry-pi-setup' is also used by raspberry-pi-setup.md
raspberry-pi-setup.md
  L1    VR005  error    name 'raspberry-pi-setup' is also used by raspberry-pi-notes.md

5 errors, 5 warnings, 2 info
```

`--json` prints the same findings as JSON. `--fail-on {error,warning,never}` sets the level at which the exit code becomes 1 (default `error`), so the command can gate a commit or a CI job.

## Claude Code hook

Add a `UserPromptSubmit` hook to the user-level `settings.json` of Claude Code (in the `.claude` directory of your home directory):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "markdown-memory-mcp hook --vault ~/notes", "timeout": 10 }
        ]
      }
    ]
  }
}
```

Run `markdown-memory-mcp index` once first. For the prompt "how do I keep the starter alive" the hook prints:

```json
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "Possibly relevant notes from the memory vault (read before acting if relevant):\n- sourdough-starter-feeding — How often to feed a sourdough starter and how to revive one that has gone sluggish (/path/to/vault/sourdough-starter-feeding.md)"}}
```

The hook is built never to get in the way of a prompt:

- It prints nothing for slash commands, prompts shorter than three words, and short acknowledgements such as "ok, thanks".
- It does nothing until an index exists. It never downloads a model and never builds an index from scratch.
- It updates the index itself only when at most `hook_max_updates` files changed. If more changed, it uses the index as it is. Run `markdown-memory-mcp index` to catch up.
- It skips notes that an index file already links to, since those are in context anyway, and notes it already injected in the same session.
- It applies `min_similarity` and `relative_margin` to the cosine score, and requires a shared word (BM25 above zero) unless the cosine reaches `strong_similarity`.
- Any error is written as one line to `hook.log` in the cache directory, and the hook exits 0 without output.

A call takes under a second on the fixture vault, most of it loading the model.

## MCP server

`markdown-memory-mcp serve` speaks MCP over stdio and works with any MCP client. Tools:

| Tool | Purpose |
|---|---|
| `search_notes(query, k=5)` | Hybrid search, with the score breakdown |
| `read_note(name)` | Full text of a note, by file name or frontmatter `name` |
| `backlinks(name)` | Notes and index files that link to a note |
| `lint_vault()` | The same findings as `markdown-memory-mcp lint` |
| `write_note(name, description, body, type=None, overwrite=False)` | Registered only with `--allow-write` |

`write_note` creates `<name>.md` in the vault root with frontmatter. Names must match `^[a-z0-9](?:[a-z0-9-]{0,79}[a-z0-9])?$` (lowercase letters, digits and inner dashes, 1 to 81 characters). It refuses to overwrite unless `overwrite=true`, never touches an index file, and refuses paths, symlinks and anything that would land outside the vault.

Claude Desktop, in `claude_desktop_config.json`. Desktop starts servers with a minimal `PATH`, so give the absolute path to the executable (the output of `which markdown-memory-mcp`, for example `/path/to/bin/markdown-memory-mcp`) and a vault path without `~`:

```json
{
  "mcpServers": {
    "markdown-memory-mcp": {
      "command": "/path/to/bin/markdown-memory-mcp",
      "args": ["serve", "--vault", "/path/to/vault"]
    }
  }
}
```

Claude Code:

```sh
claude mcp add markdown-memory-mcp -- markdown-memory-mcp serve --vault ~/notes
```

Append `--allow-write` to the arguments to expose `write_note`.

## Lint rules

| Code | Level | Check |
|---|---|---|
| VR001 | error | Broken `[[wikilink]]`. A link resolves by file name or by frontmatter `name`; `\|alias` and `#heading` are ignored. Links in code are ignored. |
| VR002 | error | An index file links to a local `.md` file that does not exist. |
| VR003 | error | No frontmatter, frontmatter that is not a valid YAML mapping, or a field from `required_fields` that is missing or empty. |
| VR004 | warning | Frontmatter `name` differs from the file name. |
| VR005 | error | Two notes use the same `name`. |
| VR006 | info | Orphan: no note and no index file links to it. |
| VR007 | warning / error | An index file is over `index_budget_bytes.soft` (warning) or `.hard` (error). |
| VR008 | warning | Near-duplicate notes (cosine at or above `duplicate_similarity`). Needs the embedding index and is skipped without it. |
| VR009 | warning | A path in backticks does not exist. Paths starting with `~/` are always checked. An absolute path is checked only if its first directory exists on this machine (`/Users`, `/etc`, `/opt`); otherwise it is taken for a URL route and skipped, as are paths with `[`, `*`, `<`, `{`, `…` or `...`, and paths with a `YYYY`, `MM`, `DD` or `XXX` placeholder. |
| VR010 | warning | An index line is longer than `max_index_line` characters. |

## Configuration

The vault is taken from `--vault`, then `$MARKDOWN_MEMORY_VAULT`, then the current directory. Settings come from `--config`, or from `.markdown-memory.toml` in the vault root. Every key is optional:

```toml
index_files = ["MEMORY.md"]
exclude = [".obsidian/**", ".trash/**"]
lint_exclude = []
model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
k = 3
min_similarity = 0.30
relative_margin = 0.08
strong_similarity = 0.55
required_fields = ["name", "description"]
max_index_line = 200
hook_max_updates = 20
duplicate_similarity = 0.92

[index_budget_bytes]
soft = 13000
hard = 15000
```

| Key | Default | Meaning |
|---|---|---|
| `index_files` | `["MEMORY.md"]` | Index files, relative to the vault. They are linted as indexes and not searched, and what they link to counts as already in context. |
| `exclude` | `[".obsidian/**", ".trash/**"]` | Glob patterns, relative to the vault, for files to ignore. Setting it replaces the default. |
| `lint_exclude` | `[]` | Glob patterns for files that `lint` does not report on, in addition to `exclude`. They are still searched and still count as link sources and targets. Useful for backup or archive folders. |
| `model` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | A multilingual model that fastembed supports, for example `minishlab/potion-multilingual-128M`. Changing it builds a new index. |
| `k` | `3` | Results for `search` and the hook. |
| `min_similarity` | `0.30` | Minimum cosine for the hook to inject a note. Without embeddings, any BM25 match counts. |
| `relative_margin` | `0.08` | The hook injects a note only if its cosine is within this distance of the best cosine over all notes, including notes it skips because an index file already links them. If the best match is already in context, weaker notes are not used as filler. |
| `strong_similarity` | `0.55` | A note that shares no word with the prompt (BM25 score 0) is injected only if its cosine reaches this value. Notes with a lexical match need only pass `min_similarity` and `relative_margin`. |
| `required_fields` | `["name", "description"]` | Frontmatter fields checked by VR003. |
| `index_budget_bytes` | `soft = 13000`, `hard = 15000` | Size limits for index files, checked by VR007. |
| `max_index_line` | `200` | Longest allowed index line, checked by VR010. |
| `hook_max_updates` | `20` | Most changed files the hook re-embeds on its own. |
| `duplicate_similarity` | `0.92` | Cosine at which VR008 reports a pair. |

`MARKDOWN_MEMORY_CACHE` sets the cache directory. Otherwise it is `$XDG_CACHE_HOME/markdown-memory-mcp` or `~/.cache/markdown-memory-mcp`. Each vault and model pair gets its own subdirectory, and the downloaded models live in `models/` next to them.

This project was called vault-recall before 0.2.0. The old config file name `.vault-recall.toml` is still read for this release, with a one-line note on stderr, when `.markdown-memory.toml` does not exist. An existing `vault-recall` cache directory is moved to the new name on first use, so the model is not downloaded again. The `VAULT_RECALL_*` environment variables are no longer read; use `MARKDOWN_MEMORY_VAULT` and `MARKDOWN_MEMORY_CACHE`.

## Privacy

Everything runs on your machine. The only network access is the one-time download of the embedding model from Hugging Face, the first time you run `markdown-memory-mcp index`. After that nothing leaves the machine: no telemetry and no calls to a hosted API. The index lives in the cache directory, never inside the vault. It holds the vectors and, in `meta.json`, the model name, the absolute path of the vault, and each note's relative file name, modification time and hash. It holds no note text. The cache directories it creates are mode 0700 and its files 0600.

## Limitations

- The index is one array that is read whole. That suits thousands of notes, not hundreds of thousands.
- The vault is read on every hook call. There is no daemon, so each call pays for Python start-up and for loading the model.
- Five-letter stemming is a heuristic. It merges unrelated words that share a prefix and splits related words that do not.
- Without the `embed` extra the hook falls back to BM25, `min_similarity` does not apply, and it injects more loosely related notes.
- Files over 1 MB are skipped. Only Markdown is read.
- Embeddings are computed from the title, `name`, `description` and the first 3000 characters of a note.
- VR009 cannot tell a URL route from a file path when the route's first segment is also a directory on your machine, such as `/usr/...` or `/opt/...`.
- The `mcp` extra is pinned below 2.0, because the server uses the FastMCP interface of the 1.x SDK.

## Development

```sh
uv sync --all-extras
uv run ruff check . && uv run ruff format --check .
uv run pytest                           # no model download; uses a deterministic hashing embedder
uv run pytest -m slow                   # also runs the real model
git config core.hooksPath tools/hooks   # run tools/leak-scan.sh before every push
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
