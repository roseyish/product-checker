"""Vector stores. Same interface for pgvector (production) and an in-memory store (tests, demos)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from app.ingest.chunker import Chunk


@dataclass
class Hit:
    chunk: Chunk
    score: float


class VectorStore(ABC):
    @abstractmethod
    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> int: ...

    @abstractmethod
    def search(self, query_vec: np.ndarray, top_k: int, brand: str | None = None) -> list[Hit]: ...

    @abstractmethod
    def count(self) -> int: ...


class InMemoryStore(VectorStore):
    def __init__(self):
        self._chunks: list[Chunk] = []
        self._vecs: np.ndarray | None = None

    def upsert(self, chunks, vectors):
        # replace any existing chunks from the same source (idempotent re-ingest)
        sources = {c.source for c in chunks}
        keep = [i for i, c in enumerate(self._chunks) if c.source not in sources]
        self._chunks = [self._chunks[i] for i in keep] + list(chunks)
        old = self._vecs[keep] if self._vecs is not None and keep else None
        self._vecs = vectors if old is None else np.vstack([old, vectors])
        return len(chunks)

    def search(self, query_vec, top_k, brand=None):
        if self._vecs is None or not len(self._chunks):
            return []
        sims = self._vecs @ query_vec.reshape(-1)
        order = np.argsort(-sims)
        hits = []
        for i in order:
            c = self._chunks[int(i)]
            if brand and c.brand and c.brand.lower() != brand.lower() and c.doc_type == "report":
                continue  # other brands' reports are not evidence for this brand
            hits.append(Hit(c, float(sims[i])))
            if len(hits) >= top_k:
                break
        return hits

    def count(self):
        return len(self._chunks)


class PgVectorStore(VectorStore):
    """PostgreSQL + pgvector with an HNSW cosine index."""

    def __init__(self, database_url: str, dim: int):
        import psycopg
        from pgvector.psycopg import register_vector

        self.conn = psycopg.connect(database_url, autocommit=True)
        register_vector(self.conn)
        self.dim = dim
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS evidence_chunks (
                    chunk_id  TEXT PRIMARY KEY,
                    source    TEXT NOT NULL,
                    brand     TEXT,
                    doc_type  TEXT NOT NULL,
                    position  INT  NOT NULL,
                    text      TEXT NOT NULL,
                    embedding VECTOR({dim}) NOT NULL
                )""")
            cur.execute("""CREATE INDEX IF NOT EXISTS evidence_chunks_hnsw
                           ON evidence_chunks USING hnsw (embedding vector_cosine_ops)""")
            cur.execute("CREATE INDEX IF NOT EXISTS evidence_chunks_brand ON evidence_chunks (brand)")

    def upsert(self, chunks, vectors):
        with self.conn.cursor() as cur:
            for c, v in zip(chunks, vectors):
                cur.execute("""INSERT INTO evidence_chunks (chunk_id, source, brand, doc_type, position, text, embedding)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (chunk_id) DO UPDATE SET text = EXCLUDED.text, embedding = EXCLUDED.embedding""",
                            (c.chunk_id, c.source, c.brand, c.doc_type, c.position, c.text, v))
        return len(chunks)

    def search(self, query_vec, top_k, brand=None):
        sql = """SELECT chunk_id, source, brand, doc_type, position, text, 1 - (embedding <=> %s) AS score
                 FROM evidence_chunks"""
        params: list = [query_vec]
        if brand:
            sql += " WHERE doc_type <> 'report' OR lower(brand) = lower(%s)"
            params.append(brand)
        sql += " ORDER BY embedding <=> %s LIMIT %s"
        params += [query_vec, top_k]
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [Hit(Chunk(r[0], r[1], r[2], r[3], r[5], r[4]), float(r[6])) for r in rows]

    def count(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM evidence_chunks")
            return cur.fetchone()[0]


def get_store(settings) -> VectorStore:
    if settings.vector_store == "pgvector":
        return PgVectorStore(settings.database_url, settings.embed_dim)
    return InMemoryStore()
