import re

_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u00a0": " ",
})

STOP = {
    "that", "this", "with", "from", "have", "been", "were", "will", "their", "which", "while",
    "also", "more", "than", "into", "such", "them", "they", "there", "these", "those", "would",
    "could", "about", "other", "some", "most", "both", "each", "being", "where", "when", "what",
    "additionally", "furthermore", "however", "concurrently", "moreover", "overall", "meanwhile",
}


def content_words(text: str) -> set[str]:
    """Meaningful words cut to 5 letters, so 'battery' and 'batteries' match."""
    words = re.findall(r"[a-z0-9]+", text.translate(_FOLD).lower())
    return {w[:5] for w in words if len(w) > 3 and w not in STOP}


def coverage(text: str, reference: str, ignore_text: str = "") -> float:
    """Share of text's meaningful words that also appear in reference."""
    words = content_words(text) - content_words(ignore_text)
    if not words:
        return 1.0
    return len(words & content_words(reference)) / len(words)