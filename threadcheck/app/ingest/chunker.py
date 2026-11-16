"""Sentence-aware sliding-window chunker. Keeps sentences intact so citations read cleanly."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    chunk_id: str
    source: str
    brand: str | None
    doc_type: str
    text: str
    position: int


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    return [s for s in _SENT_SPLIT.split(text) if s]


def chunk_text(text: str, source: str, brand: str | None, doc_type: str,
               chunk_size: int = 600, overlap: int = 80) -> list[Chunk]:
    sentences = split_sentences(text)
    chunks: list[Chunk] = []
    buf: list[str] = []
    size = 0
    pos = 0

    def flush():
        nonlocal buf, size, pos
        if not buf:
            return
        body = " ".join(buf)
        chunks.append(Chunk(f"chunk_{source}_{pos:04d}", source, brand, doc_type, body, pos))
        pos += 1
        # keep a tail of sentences as overlap for context continuity
        tail, tail_len = [], 0
        for s in reversed(buf):
            if tail_len + len(s) > overlap:
                break
            tail.insert(0, s)
            tail_len += len(s)
        buf, size = tail, tail_len

    for s in sentences:
        if size + len(s) > chunk_size and buf:
            flush()
        buf.append(s)
        size += len(s)
    flush()
    return chunks
