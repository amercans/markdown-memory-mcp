from __future__ import annotations

import json

import numpy as np
import pytest

from conftest import write_note
from vault_recall.embed import HashingEmbedder
from vault_recall.store import Store, StoreError, cache_root
from vault_recall.vault import load_vault


class CountingEmbedder(HashingEmbedder):
    def __init__(self):
        super().__init__()
        self.embedded = 0

    def embed(self, texts):
        self.embedded += len(texts)
        return super().embed(texts)


def make_store(cfg):
    return Store(cfg.vault, cfg.model)


def test_hashing_embedder_is_deterministic_and_normalised(embedder):
    a = embedder.embed(["sourdough starter feeding", "nginx cache headers"])
    b = embedder.embed(["sourdough starter feeding", "nginx cache headers"])
    assert np.array_equal(a, b)
    assert np.allclose(np.linalg.norm(a, axis=1), 1.0)
    assert float(a[0] @ a[1]) < 0.5


def test_full_index_then_incremental(cfg):
    store, emb = make_store(cfg), CountingEmbedder()
    notes = load_vault(cfg).notes
    first = store.update(notes, emb)
    assert (first.added, first.modified, first.removed) == (len(notes), 0, 0)
    assert emb.embedded == len(notes)

    again = store.update(load_vault(cfg).notes, emb)
    assert (again.added, again.modified, again.removed, again.unchanged) == (0, 0, 0, len(notes))
    assert emb.embedded == len(notes)  # nothing was embedded again


def test_add_modify_delete(cfg, vault_dir):
    store, emb = make_store(cfg), CountingEmbedder()
    store.update(load_vault(cfg).notes, emb)
    before = emb.embedded

    write_note(vault_dir, "brand-new.md", "---\nname: brand-new\ndescription: d\n---\nfresh")
    (vault_dir / "old-router-firmware.md").write_text(
        "---\nname: old-router-firmware\n---\nchanged"
    )
    (vault_dir / "name-mismatch.md").unlink()

    notes = load_vault(cfg).notes
    changes = store.changes(notes)
    assert [n.rel for n in changes.added] == ["brand-new.md"]
    assert [n.rel for n in changes.modified] == ["old-router-firmware.md"]
    assert changes.removed == ["name-mismatch.md"]

    result = store.update(notes, emb)
    assert (result.added, result.modified, result.removed) == (1, 1, 1)
    assert emb.embedded == before + 2
    vectors = store.vectors_for(notes)
    assert "name-mismatch.md" not in vectors and "brand-new.md" in vectors
    assert not store.changes(notes).count


def test_touching_a_file_without_changing_it_does_not_reembed(cfg, vault_dir):
    store, emb = make_store(cfg), CountingEmbedder()
    store.update(load_vault(cfg).notes, emb)
    before = emb.embedded
    path = vault_dir / "home-lab-dns.md"
    path.write_text(path.read_text())
    assert not store.changes(load_vault(cfg).notes).count
    assert emb.embedded == before


def test_meta_layout(cfg):
    store = make_store(cfg)
    store.update(load_vault(cfg).notes, HashingEmbedder())
    meta = json.loads((store.dir / "meta.json").read_text())
    assert {"path", "mtime", "sha256", "row"} <= set(meta["files"][0])
    assert np.load(store.dir / "vectors.npy").shape[0] == len(meta["files"])


def test_cache_is_outside_the_vault_and_keyed_by_vault_and_model(cfg, vault_dir, tmp_path):
    a = Store(cfg.vault, "model-a")
    b = Store(cfg.vault, "model-b")
    other = tmp_path / "other-vault"
    other.mkdir()
    c = Store(other, "model-a")
    assert len({a.dir, b.dir, c.dir}) == 3
    assert vault_dir not in a.dir.parents


def test_cache_inside_the_vault_is_refused(cfg):
    with pytest.raises(StoreError, match="inside the vault"):
        Store(cfg.vault, "m", root=cfg.vault / ".cache")


def test_cache_root_environment_order(tmp_path):
    assert cache_root({"VAULT_RECALL_CACHE": str(tmp_path / "a")}) == tmp_path / "a"
    env = {"XDG_CACHE_HOME": str(tmp_path / "x")}
    assert cache_root(env) == tmp_path / "x" / "vault-recall"
    assert cache_root({}).parts[-2:] == (".cache", "vault-recall")


def test_vault_is_not_written_to(cfg, vault_dir):
    def snapshot():
        return {
            p: (p.stat().st_mtime_ns, p.read_bytes()) for p in vault_dir.rglob("*") if p.is_file()
        }

    before = snapshot()
    make_store(cfg).update(load_vault(cfg).notes, HashingEmbedder())
    assert snapshot() == before


def test_corrupt_or_mismatched_index_is_treated_as_missing(cfg):
    store = make_store(cfg)
    notes = load_vault(cfg).notes
    store.update(notes, HashingEmbedder())
    (store.dir / "vectors.npy").write_bytes(b"garbage")
    assert store.vectors_for(notes) == {}
    assert store.changes(notes).count == len(notes)
    store.update(notes, HashingEmbedder())
    assert len(store.vectors_for(notes)) == len(notes)

    meta = json.loads((store.dir / "meta.json").read_text())
    meta["files"].pop()
    (store.dir / "meta.json").write_text(json.dumps(meta))
    assert store.vectors_for(notes) == {}


def test_embedder_is_required_only_when_something_changed(cfg):
    store = make_store(cfg)
    notes = load_vault(cfg).notes
    with pytest.raises(StoreError):
        store.update(notes, None)
    store.update(notes, HashingEmbedder())
    store.update(notes, None)  # nothing to embed, so no embedder needed


def test_no_temp_files_left_behind(cfg):
    store = make_store(cfg)
    store.update(load_vault(cfg).notes, HashingEmbedder())
    assert sorted(p.name for p in store.dir.glob("*")) == ["meta.json", "vectors.npy"]
