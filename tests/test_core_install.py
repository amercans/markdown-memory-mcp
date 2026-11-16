"""The core install has no numpy or fastembed: search, lint and hook must still work."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

WRAPPER = (
    "import sys; sys.modules['numpy'] = None; sys.modules['fastembed'] = None; "
    "from markdown_memory_mcp.cli import main; sys.exit(main(sys.argv[1:]))"
)


@pytest.fixture
def run(vault_dir, tmp_path):
    env = {**os.environ, "MARKDOWN_MEMORY_CACHE": str(tmp_path / "cache")}

    def _run(*args, stdin=""):
        return subprocess.run(
            [sys.executable, "-c", WRAPPER, *args, "--vault", str(vault_dir)],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    return _run


def test_search_without_numpy(run):
    proc = run("search", "nginx cache headers", "--json", "-k", "1")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)[0]["name"] == "nginx-cache-headers"
    assert "BM25 only" in proc.stderr


def test_lint_without_numpy(run):
    proc = run("lint", "--fail-on", "never")
    assert proc.returncode == 0, proc.stderr
    assert "VR001" in proc.stdout


def test_hook_without_numpy(run, tmp_path):
    payload = json.dumps({"prompt": "how do I keep the sourdough starter alive", "session_id": "c"})
    proc = run("hook", stdin=payload)
    assert proc.returncode == 0
    assert "sourdough-starter-feeding" in proc.stdout
    assert not (tmp_path / "cache" / "hook.log").exists()


def test_index_explains_the_missing_extra(run):
    proc = run("index")
    assert proc.returncode == 1
    assert "markdown-memory-mcp[all]" in proc.stderr
