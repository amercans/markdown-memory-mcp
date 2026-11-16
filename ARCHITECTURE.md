# Architecture

This document explains how markdown-memory-mcp is put together, what it promises, what it protects against and why some choices were made the way they were.

## Components

| Module | Role |
|---|---|
| `config.py` | Defaults, `.markdown-memory.toml` parsing and validation, vault location |
| `vault.py` | Note discovery, frontmatter, link extraction, link resolution |
| `text.py` | Folding, tokenising and prefix stemming |
| `bm25.py` | Okapi BM25 in pure Python |
| `embed.py` | `Embedder` protocol, the fastembed backend, a deterministic hashing embedder for tests |
| `store.py` | The embedding index in a cache directory, with incremental updates |
| `search.py` | Corpus construction and hybrid ranking with Reciprocal Rank Fusion |
| `lint.py` | Rules VR001-VR010 |
| `hook.py` | The `UserPromptSubmit` hook |
| `mcp_server.py` | MCP tools as plain methods, plus the FastMCP wiring |
| `cli.py` | argparse front end |

Only PyYAML is a hard dependency. numpy and fastembed (`embed`) and the MCP SDK (`mcp`) are imported lazily, so the core runs without them.

## Flows

**Search.**
1. `load_vault` reads every Markdown file that is not excluded, parses frontmatter and hashes the bytes.
2. A BM25 index is built in memory from the folded, stemmed text of each note.
3. If the embedding extra is installed and an index exists, the index is brought up to date (see below), the query is embedded, and cosine is computed against every stored vector.
4. Both rankings are fused with RRF (k = 60). BM25 contributes only notes with a score above zero. The embedding ranking contains every note that has a vector.

**Index update.** Each stored entry holds the relative path, the SHA-256 of the file, its mtime and the row in `vectors.npy`. A file is re-embedded if its path is new or its hash differs. A file that was only touched is not. Entries for deleted files are dropped. The vault is read in full on every run anyway, so the hash, not the mtime, is the source of truth. The mtime is stored for inspection.

**Hook.**
1. Parse stdin. Skip slash commands, prompts under three words and short acknowledgements.
2. Build the corpus with `require_index`: if embeddings are installed but no index exists, stop. The hook never builds an index and never downloads a model; the model is loaded with `local_files_only`.
3. Re-embed changed files only if there are at most `hook_max_updates`. Otherwise use the stale index. Notes without a current vector can then only match lexically, and the hook refuses them (see invariants).
4. Rank over all notes and find the best cosine. Then drop notes that an index file links to and notes already injected in this session, and keep up to `k` of the rest whose cosine is at least `min_similarity` and within `relative_margin` of the best. A note with no lexical match (BM25 score 0) must also reach `strong_similarity`, because embeddings alone rate unrelated topics too close. A strong match that is skipped therefore still stops weaker notes from being injected as filler.
5. Print the JSON object, record what was injected in the session state file.

**MCP.** `VaultTools` holds the logic. `build_server` registers bound methods with FastMCP, and registers `write_note` only if `allow_write` is true. Tests call the methods directly.

## Invariants

- **The vault is read-only** for every command. The one exception is `write_note`, which exists only under `--allow-write`.
- **The cache is never inside the vault.** `Store` refuses a cache root that equals the vault or sits below it.
- **Index writes are atomic.** Each file is written to a temporary file in the same directory and moved into place with `os.replace`. `vectors.npy` is replaced before `meta.json`. If a crash falls between the two, the row count no longer matches and the index is treated as missing, so it is rebuilt on the next `index`.
- **The hook never blocks a prompt.** Every exception is caught, logged as one line to `hook.log` and turned into exit code 0 with no output. The log is trimmed when it grows past 64 KB.
- **The hook does no heavy work.** No model download, no full indexing, and a bounded number of re-embeddings.
- **In embedding mode the hook requires a cosine score.** A note without a current vector is never injected on a lexical match alone, because BM25 on its own is a much weaker signal than the threshold the user configured.
- **`write_note` stays inside the vault root.** Names match `^[a-z0-9](?:[a-z0-9-]{0,79}[a-z0-9])?$` (lowercase letters, digits and inner dashes, 1 to 81 characters), so a name cannot contain a separator or a dot. The target is created directly under the vault root. A symlink at the target is refused. A target that equals an index file is refused, compared case-insensitively and, when both exist, by `os.path.samefile`, so `memory.md` cannot replace `MEMORY.md` on a case-insensitive file system. Creation without `overwrite` uses a hard link from a temporary file, so it fails rather than replace a file that appeared in the meantime.
- **`read_note` never takes a path.** It looks the name up among the discovered notes. Input containing a separator, `..`, a drive-like prefix or a NUL byte is rejected. Discovery skips symlinks that resolve outside the vault, and the read re-checks the resolved path.

## State files

| Path | Meaning |
|---|---|
| `<cache>/<hash>/vectors.npy` | float32 matrix, one L2-normalised row per note |
| `<cache>/<hash>/meta.json` | Format version, model, vault path, and per note: path, mtime, sha256, row |
| `<cache>/<hash>/sessions/*.json` | Per hook session, the notes already injected. Files older than 7 days are deleted whenever one is written. |
| `<cache>/hook.log` | One line per hook failure |
| `<cache>/models/` | Downloaded embedding models |

`<hash>` is the first 16 hex digits of SHA-256 over the absolute vault path and the model name.

## Threat model

The notes are the user's own, but they can contain text from the web or from earlier assistant sessions, and an MCP client is driven by a model. The relevant threats are accidental or induced writes outside the vault, and exposure of note content.

| Threat | Mitigation | Residual risk |
|---|---|---|
| A client tool call reads or writes outside the vault (`../`, absolute paths, symlinks) | Names are validated and looked up, never joined into a path. Symlinks that leave the vault are skipped or refused. Writes go only to the vault root. | A symlinked directory inside the vault is not followed, but a symlink the user placed deliberately to a file outside is also ignored, not read. |
| A model overwrites a note or the index | `write_note` is off by default. `overwrite` must be set explicitly. Index files are never written. | With `--allow-write` and `overwrite=true`, a model can replace any non-index note in the vault root. Keep the vault in version control. |
| A crafted note breaks the hook or the lint | YAML is parsed with `safe_load`. Parse failures are reported as findings. The hook swallows exceptions. | A note can still waste CPU. Files over 1 MB are skipped. |
| Injected notes carry instructions | The hook adds names, descriptions and paths, not note bodies, under a header that tells the assistant to read before acting. | A description is free text written by the user and, with `write_note`, possibly by a model. |
| Note content leaves the machine | No network code except the model download in fastembed. The cache holds vectors, the absolute vault path, relative file names, mtimes and hashes, not text. Cache directories are created with mode 0700 and files with 0600, including `hook.log` and the session files. | Vectors can leak some information about the text, and file names and the vault path are readable by anyone who can read the cache. Directories that already existed are not changed. |
| The model download is tampered with | Downloaded over HTTPS from Hugging Face by fastembed. | Trust in that source and in fastembed's checks. |

## Design decisions

- **Hybrid, not embeddings alone.** Embeddings find paraphrases. BM25 finds exact identifiers, error strings and names that a small multilingual model embeds poorly. RRF needs no score calibration between the two. Both rankings are cheap, and each covers the other's blind spots.
- **Prefix stemming instead of a language-specific stemmer.** One rule works for English and for agglutinative languages such as Turkish without a dependency or a language setting. The cost is occasional over-merging.
- **I-folding that also folds Turkish.** `İ`, `I` and `ı` all become `i`. This is wrong for strict Turkish orthography and right for retrieval, where a query typed on an English keyboard has to match a note written with Turkish capitals.
- **Reading the vault on every call instead of a daemon.** It keeps the tool stateless and the files authoritative, with nothing to start, stop or invalidate. At a few hundred notes the read and the BM25 build cost milliseconds. The model load dominates.
- **BM25 is not cached.** It is rebuilt from the files each time. A cached lexical index would have to be invalidated in the same way as the vectors, for a gain that is small next to the model load.
- **The index lives outside the vault.** A vault is often synced, committed or shared. A binary cache there would produce conflicts and noise. Keying the cache by vault path and model keeps vaults and models apart.
- **The hook is allowed to be stale.** After a bulk edit the hook uses the old vectors instead of blocking a prompt on a long embedding run.
- **Index files are excluded from search.** They are loaded into every session, so returning them adds nothing. What they link to is excluded from the hook for the same reason.
- **A deterministic hashing embedder for tests.** CI never downloads a model, and results are reproducible. One test marked `slow` runs the real model.
- **`mcp` is pinned below 2.0.** The 2.x SDK renamed the server class. The pin can be lifted when the code is ported.
