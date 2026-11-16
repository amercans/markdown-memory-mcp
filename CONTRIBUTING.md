# Contributing

Bug reports and pull requests are welcome.

- Keep changes small and focused, and explain the problem the change solves.
- Set up with `uv sync --all-extras`. Before opening a pull request run
  `uv run ruff check .`, `uv run ruff format --check .` and `uv run pytest` (CI runs them on
  Python 3.11 to 3.13, on Linux and macOS).
- Tests must not download a model. Use `HashingEmbedder` from `vault_recall.embed`. A test that
  needs the real model gets `@pytest.mark.slow` and runs with `uv run pytest -m slow`.
- Changes to the fixture vault in `tests/fixtures/vault` can break many tests, because it holds
  one deliberate defect per lint rule. Check the lint tests when you touch it.
- Never include real names, paths, addresses, e-mail addresses or note contents in code, tests,
  fixtures, docs or commit messages. Use `example.com`, the documentation address ranges
  (`192.0.2.0/24`, `198.51.100.0/24`, `203.0.113.0/24`) and invented notes. `tools/leak-scan.sh`
  checks files and history for this; enable it before every push with
  `git config core.hooksPath tools/hooks`. It also reads a private pattern list outside the
  repository, `$VR_LEAK_PATTERNS` or `~/.config/vault-recall/forbidden-patterns.txt`.
- Comments explain why, not what. Behaviour changes that users notice belong in `CHANGELOG.md`
  under "Unreleased".

By contributing you agree that your contribution is licensed under the MIT License.
