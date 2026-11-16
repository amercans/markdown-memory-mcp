import pytest

from markdown_memory_mcp.config import ConfigError, load_config, resolve_vault


def test_defaults(vault_dir):
    cfg = load_config(str(vault_dir))
    assert cfg.index_files == ("MEMORY.md",)
    assert cfg.exclude == (".obsidian/**", ".trash/**")
    assert (cfg.k, cfg.min_similarity, cfg.max_index_line, cfg.hook_max_updates) == (
        3,
        0.30,
        200,
        20,
    )
    assert (cfg.budget_soft, cfg.budget_hard) == (13000, 15000)
    assert cfg.required_fields == ("name", "description")


def test_toml_overrides(vault_dir):
    (vault_dir / ".markdown-memory.toml").write_text(
        'index_files = ["INDEX.md"]\nk = 5\nmin_similarity = 0.5\n'
        'model = "minishlab/potion-multilingual-128M"\n'
        'required_fields = ["name"]\n[index_budget_bytes]\nsoft = 100\nhard = 200\n'
    )
    cfg = load_config(str(vault_dir))
    assert cfg.index_files == ("INDEX.md",)
    assert (cfg.k, cfg.min_similarity) == (5, 0.5)
    assert cfg.model.startswith("minishlab/")
    assert (cfg.budget_soft, cfg.budget_hard) == (100, 200)


def test_explicit_config_path(vault_dir, tmp_path):
    other = tmp_path / "other.toml"
    other.write_text("k = 7\n")
    assert load_config(str(vault_dir), str(other)).k == 7
    with pytest.raises(ConfigError, match="not found"):
        load_config(str(vault_dir), str(tmp_path / "missing.toml"))


@pytest.mark.parametrize(
    "text",
    [
        "k = 0\n",
        'k = "3"\n',
        "k = true\n",
        "min_similarity = 2\n",
        "unknown_key = 1\n",
        "index_files = []\n",
        'exclude = "x"\n',
        "[index_budget_bytes]\nsoft = 10\nhard = 5\n",
        "[index_budget_bytes]\nmedium = 1\n",
        "k = [\n",
    ],
)
def test_invalid_config_is_rejected(vault_dir, text):
    (vault_dir / ".markdown-memory.toml").write_text(text)
    with pytest.raises(ConfigError):
        load_config(str(vault_dir))


def test_vault_resolution_order(vault_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_vault(None) == tmp_path.resolve()
    monkeypatch.setenv("MARKDOWN_MEMORY_VAULT", str(vault_dir))
    assert resolve_vault(None) == vault_dir.resolve()
    assert resolve_vault(str(tmp_path)) == tmp_path.resolve()


def test_missing_vault(tmp_path):
    with pytest.raises(ConfigError, match="not a directory"):
        resolve_vault(str(tmp_path / "nope"))


def test_errors_name_the_file_and_use_correct_grammar(vault_dir):
    path = vault_dir / ".markdown-memory.toml"
    path.write_text('k = "3"\n')
    with pytest.raises(ConfigError) as exc:
        load_config(str(vault_dir))
    assert str(exc.value) == f"{path}: k must be an int"
    path.write_text('min_similarity = "x"\n')
    with pytest.raises(ConfigError, match="min_similarity must be a float"):
        load_config(str(vault_dir))


def test_legacy_config_file_is_read_with_a_deprecation_note(vault_dir, capsys):
    (vault_dir / ".vault-recall.toml").write_text("k = 7\n")
    assert load_config(str(vault_dir)).k == 7
    err = capsys.readouterr().err
    assert "deprecated config file .vault-recall.toml" in err
    assert "rename it to .markdown-memory.toml" in err and err.count("\n") == 1


def test_new_config_file_wins_and_is_silent(vault_dir, capsys):
    (vault_dir / ".vault-recall.toml").write_text("k = 7\n")
    (vault_dir / ".markdown-memory.toml").write_text("k = 4\n")
    assert load_config(str(vault_dir)).k == 4
    assert capsys.readouterr().err == ""
