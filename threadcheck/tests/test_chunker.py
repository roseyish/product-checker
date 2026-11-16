from app.ingest.chunker import chunk_text, split_sentences


def test_sentences_stay_intact():
    text = "First sentence here. Second one follows! Third asks a question? Fourth ends."
    assert split_sentences(text) == ["First sentence here.", "Second one follows!",
                                     "Third asks a question?", "Fourth ends."]


def test_chunks_respect_size_and_overlap():
    text = " ".join(f"Sentence {i} about recycled polyester audits." for i in range(60))
    chunks = chunk_text(text, "src", "B", "report", chunk_size=200, overlap=60)
    assert len(chunks) > 5
    assert all(len(c.text) <= 260 for c in chunks)  # size + one sentence tolerance
    # overlap: the last sentence of chunk i appears in chunk i+1
    for a, b in zip(chunks, chunks[1:]):
        last = a.text.split(". ")[-1].rstrip(".")
        assert last in b.text


def test_chunk_ids_are_unique_and_ordered():
    chunks = chunk_text("A. B. C. D. E. F.", "doc", None, "report", chunk_size=5, overlap=0)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))
    assert [c.position for c in chunks] == list(range(len(chunks)))
