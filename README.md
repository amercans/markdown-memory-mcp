# vault-recall

[![CI](https://github.com/amercans/vault-recall/actions/workflows/ci.yml/badge.svg)](https://github.com/amercans/vault-recall/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/amercans/vault-recall/badge)](https://scorecard.dev/viewer/?uri=github.com/amercans/vault-recall)
[![Release](https://img.shields.io/github/v/release/amercans/vault-recall)](https://github.com/amercans/vault-recall/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Local recall, lint and an MCP server for a folder of Markdown notes that serves as an AI assistant's long-term memory. It works with Obsidian-style vaults: YAML frontmatter, `[[wikilinks]]`, and an index file such as `MEMORY.md` that links to the notes. It runs as a [Claude Code](https://docs.claude.com/en/docs/claude-code) hook that adds the notes relevant to each prompt, as a command line tool, and as an MCP server for any MCP client.

The files stay the source of truth. vault-recall never writes to your vault. The only exception is the MCP `write_note` tool, which exists only if you start the server with `--allow-write`.

## Why

An index file that is loaded into every session has to stay small. As a vault grows, older notes get moved out of the index, and from then on the assistant finds them only when it happens to search for the exact words the note uses. "Keep the starter alive" does not match a note titled "sourdough starter feeding", and a Turkish note is not found by an English query.

vault-recall closes that gap in two ways:

- **Recall by meaning.** It ranks notes with BM25 and with multilingual embeddings and merges both rankings. The hook injects the few notes that match the current prompt and are not already in the index.
- **A healthier vault.** `vault-recall lint` finds broken links, notes without a description, duplicate names, orphans, an index that outgrew its budget, and notes that are near-copies of each other.

## How it works

```
 prompt ──▶ UserPromptSubmit hook ──▶ vault-recall hook
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
- **Semantic side.** [fastembed](https://github.com/qdrant/fastembed) runs a small multilingual model locally (ONNX, no GPU). Vectors are kept in a cache directory outside the vault and are updated incrementally.
- **Fusion.** Reciprocal Rank Fusion with k = 60 over the two rankings. `search --json` returns the score breakdown.
- **Without the `embed` extra** everything still works on BM25 alone, except the near-duplicate lint rule.

The design, invariants and threat model are in [ARCHITECTURE.md](ARCHITECTURE.md).

## Requirements

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/) (pip works too).
- For embeddings: about 220 MB of disk for the default model, downloaded on the first `index`.

## Install

```sh
uv tool install "vault-recall[all] @ git+https://github.com/amercans/vault-recall"
```

Extras: `embed` (fastembed and numpy), `mcp` (the MCP server), `all` (both). The core install depends on PyYAML only.

## Quick start

The examples were run on the small vault in `tests/fixtures/vault`. Replace the path with your own, for example `~/notes`. If you run a command from inside the vault, or set `VAULT_RECALL_VAULT`, `--vault` can be left out.

Build the index. The first run downloads the model. Later runs embed only new and changed notes.

```console
$ vault-recall index --vault ~/notes
13 notes: 13 added, 0 updated, 0 removed, 0 unchanged (~/.cache/vault-recall/40e0aaef2855ad2f)
```

Search. Neither "keep" nor "alive" appears in the note that comes first:

```console
$ vault-recall search "how do I keep the starter alive" --vault ~/notes -k 1
1. sourdough-starter-feeding  [rrf 0.0328  cosine 0.47  bm25 7.23]
   sourdough-starter-feeding.md
   How often to feed a sourdough starter and how to revive one that has gone sluggish
```

A Turkish query finds the Turkish note:

```console
$ vault-recall search "simit nasıl yapılır" --vault ~/notes -k 1
1. istanbul-simit  [rrf 0.0328  cosine 0.13  bm25 3.23]
   istanbul-simit.md
   İstanbul usulü simit tarifi, pekmezli su ve susam ile
```

Lint. The fixture vault contains one deliberate defect for each rule, apart from VR007:

```console
$ vault-recall lint --vault ~/notes
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

5 errors, 5 warnings, 2 notes
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
          { "type": "command", "command": "vault-recall hook --vault ~/notes" }
        ]
      }
    ]
  }
}
```

Run `vault-recall index` once first. For the prompt "how do I keep the starter alive" the hook prints:

```json
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "Possibly relevant notes from the memory vault (read before acting if relevant):\n- sourdough-starter-feeding — How often to feed a sourdough starter and how to revive one that has gone sluggish (/path/to/vault/sourdough-starter-feeding.md)"}}
```

The hook is built never to get in the way of a prompt:

- It prints nothing for slash commands, prompts shorter than three words, and short acknowledgements such as "ok, thanks".
- It does nothing until an index exists. It never downloads a model and never builds an index from scratch.
- It updates the index itself only when at most `hook_max_updates` files changed. If more changed, it uses the index as it is. Run `vault-recall index` to catch up.
- It skips notes that an index file already links to, since those are in context anyway, and notes it already injected in the same session.
- It applies `min_similarity` and `relative_margin` to the cosine score.
- Any error is written as one line to `hook.log` in the cache directory, and the hook exits 0 without output.

A call takes under a second on the fixture vault, most of it loading the model.

## MCP server

`vault-recall serve` speaks MCP over stdio and works with any MCP client. Tools:

| Tool | Purpose |
|---|---|
| `search_notes(query, k=5)` | Hybrid search, with the score breakdown |
| `read_note(name)` | Full text of a note, by file name or frontmatter `name` |
| `backlinks(name)` | Notes and index files that link to a note |
| `lint_vault()` | The same findings as `vault-recall lint` |
| `write_note(name, description, body, type=None, overwrite=False)` | Registered only with `--allow-write` |

`write_note` creates `<name>.md` in the vault root with frontmatter. Names must match `^[a-z0-9][a-z0-9-]{0,80}$`. It refuses to overwrite unless `overwrite=true`, never touches an index file, and refuses paths, symlinks and anything that would land outside the vault.

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vault-recall": {
      "command": "vault-recall",
      "args": ["serve", "--vault", "/path/to/vault"]
    }
  }
}
```

Claude Code:

```sh
claude mcp add vault-recall -- vault-recall serve --vault ~/notes
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
| VR009 | warning | A path in backticks, absolute or starting with `~/`, does not exist. |
| VR010 | warning | An index line is longer than `max_index_line` characters. |

## Configuration

The vault is taken from `--vault`, then `$VAULT_RECALL_VAULT`, then the current directory. Settings come from `--config`, or from `.vault-recall.toml` in the vault root. Every key is optional:

```toml
index_files = ["MEMORY.md"]
exclude = [".obsidian/**", ".trash/**"]
model = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
k = 3
min_similarity = 0.40
relative_margin = 0.08
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
| `model` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | A multilingual model that fastembed supports, for example `minishlab/potion-multilingual-128M`. Changing it builds a new index. |
| `k` | `3` | Results for `search` and the hook. |
| `min_similarity` | `0.40` | Minimum cosine for the hook to inject a note. Without embeddings, any BM25 match counts. |
| `relative_margin` | `0.08` | The hook injects a note only if its cosine is within this distance of the best cosine over all notes, including notes it skips because an index file already links them. If the best match is already in context, weaker notes are not used as filler. |
| `required_fields` | `["name", "description"]` | Frontmatter fields checked by VR003. |
| `index_budget_bytes` | `soft = 13000`, `hard = 15000` | Size limits for index files, checked by VR007. |
| `max_index_line` | `200` | Longest allowed index line, checked by VR010. |
| `hook_max_updates` | `20` | Most changed files the hook re-embeds on its own. |
| `duplicate_similarity` | `0.92` | Cosine at which VR008 reports a pair. |

`VAULT_RECALL_CACHE` sets the cache directory. Otherwise it is `$XDG_CACHE_HOME/vault-recall` or `~/.cache/vault-recall`. Each vault and model pair gets its own subdirectory, and the downloaded models live in `models/` next to them.

## Privacy

Everything runs on your machine. The only network access is the one-time download of the embedding model from Hugging Face, the first time you run `vault-recall index`. After that nothing leaves the machine: no telemetry and no calls to a hosted API. The index lives in the cache directory, never inside the vault. It holds vectors, file hashes and relative file names, not note text.

## Limitations

- The index is one array that is read whole. That suits thousands of notes, not hundreds of thousands.
- The vault is read on every hook call. There is no daemon, so each call pays for Python start-up and for loading the model.
- Five-letter stemming is a heuristic. It merges unrelated words that share a prefix and splits related words that do not.
- Without the `embed` extra the hook falls back to BM25, `min_similarity` does not apply, and it injects more loosely related notes.
- Files over 1 MB are skipped. Only Markdown is read.
- Embeddings are computed from the title, `name`, `description` and the first 3000 characters of a note.
- VR009 checks every absolute-looking path in backticks, so URL routes such as `/api/v1/users` are reported too.
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
