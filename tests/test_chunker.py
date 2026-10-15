from tools.chunker import chunk_text


def _paras(n):
    return "\n".join(f"P{i:03d} " + " ".join(["word"] * 19) for i in range(n))


def test_empty():
    assert chunk_text("") == []


def test_short_text_is_one_chunk():
    t = "A short but valid paragraph of text."
    assert chunk_text(t, min_chars=10) == [t]


def test_chunks_respect_max_and_are_verbatim():
    text = _paras(40)
    chunks = chunk_text(text, max_chars=400, overlap=150, min_chars=50)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)
    assert all(c in text for c in chunks)          # never rewritten


def test_overlap_repeats_last_paragraph():
    chunks = chunk_text(_paras(40), max_chars=400, overlap=150, min_chars=50)
    assert chunks[0].split("\n")[-1] == chunks[1].split("\n")[0]


def test_no_paragraph_is_lost():
    text = _paras(40)
    joined = "\n".join(chunk_text(text, max_chars=400, overlap=150, min_chars=50))
    assert all(p in joined for p in text.splitlines())


def test_long_paragraph_splits_on_sentences():
    par = " ".join(f"This is sentence number {i}." for i in range(200))
    chunks = chunk_text(par, max_chars=500, overlap=50, min_chars=50)
    assert len(chunks) > 1 and all(len(c) <= 500 for c in chunks)