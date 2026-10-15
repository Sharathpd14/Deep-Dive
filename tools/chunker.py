import re

_SENT = re.compile(r"(?<=[.!?])\s+")


def _split_long(par: str, max_chars: int) -> list[str]:
    """Split an oversized paragraph on sentence boundaries."""
    parts, cur = [], ""
    for s in _SENT.split(par):
        while len(s) > max_chars:                      # runaway sentence: hard slice
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(s[:max_chars])
            s = s[max_chars:]
        if cur and len(cur) + 1 + len(s) > max_chars:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}" if cur else s
    if cur:
        parts.append(cur)
    return parts


def chunk_text(
    text: str, max_chars: int = 2000, overlap: int = 200, min_chars: int = 200
) -> list[str]:
    """Paragraph-aware chunks with overlap. Chunk text is never rewritten,
    so the verifier's quote check can match it verbatim."""
    paras = [p.strip() for p in text.splitlines() if p.strip()]
    units: list[str] = []
    for p in paras:
        units.extend([p] if len(p) <= max_chars else _split_long(p, max_chars))

    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for u in units:
        add = len(u) + (1 if cur else 0)
        if cur and cur_len + add > max_chars:
            chunks.append("\n".join(cur))
            tail, tl = [], 0                            # carry overlap into next chunk
            for prev in reversed(cur):
                if tl + len(prev) > overlap:
                    break
                tail.insert(0, prev)
                tl += len(prev) + 1
            if tail and sum(map(len, tail)) + len(tail) + len(u) > max_chars:
                tail = []
            cur, cur_len = tail, (sum(map(len, tail)) + len(tail) - 1 if tail else 0)
            add = len(u) + (1 if cur else 0)
        cur.append(u)
        cur_len += add
    if cur:
        chunks.append("\n".join(cur))

    # drop tiny trailing fragments (mostly overlap), but never drop the only chunk
    if len(chunks) > 1:
        chunks = [c for i, c in enumerate(chunks) if i == 0 or len(c) >= min_chars]
    return chunks