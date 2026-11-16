import numpy as np

from app.ingest.chunker import Chunk
from app.providers.embeddings import HashEmbedder
from app.retrieval.store import InMemoryStore


def _chunks():
    return [Chunk("c1", "brand-a-report", "BrandA", "report", "62% of polyester is recycled and GRS certified.", 0),
            Chunk("c2", "brand-b-report", "BrandB", "report", "Our wool is Responsible Wool Standard certified.", 0),
            Chunk("c3", "guidance", None, "regulation", "Carbon neutral claims must disclose offsets.", 0)]


def test_search_ranks_relevant_chunk_first():
    emb, store = HashEmbedder(), InMemoryStore()
    ch = _chunks()
    store.upsert(ch, emb.embed([c.text for c in ch]))
    hits = store.search(emb.embed(["recycled polyester GRS certified"])[0], top_k=3)
    assert hits[0].chunk.chunk_id == "c1"


def test_brand_filter_excludes_other_brands_but_keeps_regulation():
    emb, store = HashEmbedder(), InMemoryStore()
    ch = _chunks()
    store.upsert(ch, emb.embed([c.text for c in ch]))
    hits = store.search(emb.embed(["certified"])[0], top_k=5, brand="BrandA")
    ids = {h.chunk.chunk_id for h in hits}
    assert "c2" not in ids and "c1" in ids and "c3" in ids


def test_reingest_same_source_is_idempotent():
    emb, store = HashEmbedder(), InMemoryStore()
    ch = _chunks()
    store.upsert(ch, emb.embed([c.text for c in ch]))
    store.upsert(ch[:1], emb.embed([ch[0].text]))
    assert store.count() == 3


def test_embeddings_are_unit_norm():
    v = HashEmbedder().embed(["hello world", "recycled polyester"])
    assert np.allclose(np.linalg.norm(v, axis=1), 1.0, atol=1e-5)
