"""Load plain text, markdown, or PDF into a string."""
from __future__ import annotations

from pathlib import Path


def load_document(path: str | Path) -> str:
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(p))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return p.read_text(encoding="utf-8", errors="ignore")
