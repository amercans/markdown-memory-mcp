#!/bin/bash
# Installs markdown-memory-mcp WITHOUT any extra into a throw-away environment and runs the commands
# that must work on BM25 alone. Proves the core install does not need numpy or fastembed.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$PWD"
VAULT="$ROOT/tests/fixtures/vault"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export MARKDOWN_MEMORY_CACHE="$WORK/cache"

uv venv --quiet "$WORK/venv"
uv pip install --quiet --python "$WORK/venv/bin/python" "$ROOT"
BIN="$WORK/venv/bin/markdown-memory-mcp"
if "$WORK/venv/bin/python" -c 'import numpy' 2>/dev/null; then
  echo "core-smoke: numpy is installed in the core environment" >&2
  exit 1
fi

out="$("$BIN" search "nginx cache headers" --vault "$VAULT" -k 1 2>"$WORK/err")"
grep -q '^1\. nginx-cache-headers' <<<"$out" || { echo "core-smoke: search failed: $out" >&2; exit 1; }
grep -q 'BM25 only' "$WORK/err" || { echo "core-smoke: no BM25 notice" >&2; exit 1; }

"$BIN" lint --vault "$VAULT" --fail-on never >"$WORK/lint"
grep -q 'VR001' "$WORK/lint" || { echo "core-smoke: lint failed" >&2; exit 1; }

hook="$(echo '{"prompt":"how do I keep the sourdough starter alive","session_id":"smoke"}' | "$BIN" hook --vault "$VAULT")"
grep -q 'sourdough-starter-feeding' <<<"$hook" || { echo "core-smoke: hook failed: $hook" >&2; exit 1; }
[ ! -s "$WORK/cache/hook.log" ] || { echo "core-smoke: hook logged an error" >&2; cat "$WORK/cache/hook.log" >&2; exit 1; }

if "$BIN" index --vault "$VAULT" 2>"$WORK/err"; then
  echo "core-smoke: index should refuse without the embed extra" >&2
  exit 1
fi
grep -q 'markdown-memory-mcp\[all\]' "$WORK/err"
echo "core-smoke: ok"
