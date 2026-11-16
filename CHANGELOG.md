# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the
project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-16

### Added
- `vault-recall index`: incremental embedding index in a cache directory outside the vault.
- `vault-recall search`: hybrid ranking with BM25 and embedding cosine, fused with Reciprocal
  Rank Fusion, with a score breakdown in `--json`.
- `vault-recall lint`: rules VR001-VR010, human and JSON output, `--fail-on`.
- `vault-recall hook`: Claude Code `UserPromptSubmit` hook that injects relevant notes that the
  index files do not already link to, once per session, and stays silent on any failure.
- `vault-recall serve`: MCP server over stdio with `search_notes`, `read_note`, `backlinks` and
  `lint_vault`, and `write_note` only with `--allow-write`.
- `.vault-recall.toml` configuration.
- Works on BM25 alone when the `embed` extra is not installed.

[Unreleased]: https://github.com/amercans/vault-recall/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/amercans/vault-recall/releases/tag/v0.1.0
