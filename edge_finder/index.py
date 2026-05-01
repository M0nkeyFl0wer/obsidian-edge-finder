"""Pure-Python TF-IDF index + top-K candidate generator.

No sklearn / numpy. Uses dict-based sparse vectors and an inverted index for
candidate retrieval. Plenty fast for vaults up to ~10k notes.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .walker import Note

# Minimal English stopword list — kept short on purpose. Aggressive stopword
# removal hurts recall on terse meeting notes.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "of", "to",
    "in", "on", "at", "for", "with", "from", "by", "as", "is", "was", "were",
    "are", "be", "been", "being", "this", "that", "these", "those", "it",
    "its", "we", "us", "our", "i", "you", "your", "they", "them", "their",
    "he", "she", "his", "her", "him", "do", "does", "did", "have", "has",
    "had", "will", "would", "could", "should", "can", "may", "might", "not",
    "no", "yes", "so", "very", "just", "also", "than", "too", "more", "most",
    "some", "any", "all", "each", "every", "such", "only", "own", "same",
    "about", "into", "out", "up", "down", "over", "under", "again", "further",
    "what", "which", "who", "whom", "where", "when", "why", "how",
}

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
_HEADING_LINE_RE = re.compile(r"^#{1,6}\s.*$", re.MULTILINE)
_LIST_BULLET_RE = re.compile(r"^[-*+]\s", re.MULTILINE)

# Template tokens that show up in every Granola/meeting note and carry no
# semantic signal. Stripped before tokenization regardless of corpus stats.
_TEMPLATE_TOKENS = {
    "summary", "attendees", "notes", "action", "items", "transcript",
    "agenda", "minutes", "decisions", "follow", "ups", "discussion",
}


def tokenize(text: str, extra_stopwords: set[str] | None = None) -> list[str]:
    # Drop heading lines (## Summary etc.) — they're template noise
    text = _HEADING_LINE_RE.sub("", text)
    text = _LIST_BULLET_RE.sub("", text)
    extra = extra_stopwords or set()
    out: list[str] = []
    for t in _TOKEN_RE.findall(text):
        low = t.lower()
        if low in _STOPWORDS or low in _TEMPLATE_TOKENS or low in extra:
            continue
        out.append(low)
    return out


@dataclass
class TfIdfIndex:
    docs: dict[str, dict[str, float]]      # path → {term: tfidf}
    norms: dict[str, float]                # path → vector norm
    inverted: dict[str, list[str]]         # term → list of paths containing it
    df: dict[str, int]                     # term → document frequency
    n_docs: int

    @classmethod
    def build(cls, notes: list[Note]) -> "TfIdfIndex":
        n = len(notes)
        # Pass 0: pre-tokenize once to learn vault-specific stopwords.
        # Any term appearing in >40% of notes adds nothing to discrimination.
        prelim_df: Counter = Counter()
        prelim_per_doc: dict[str, list[str]] = {}
        for note in notes:
            text = note.title + "\n" + " ".join(note.aliases) + "\n" + note.body
            toks = tokenize(text)
            prelim_per_doc[note.relpath] = toks
            prelim_df.update(set(toks))
        threshold = max(2, int(0.40 * n))
        learned_stopwords = {t for t, c in prelim_df.items() if c >= threshold}

        # Pass 1: re-tokenize with learned stopwords, build df
        tf_per_doc: dict[str, Counter] = {}
        df: Counter = Counter()
        for path, toks in prelim_per_doc.items():
            kept = [t for t in toks if t not in learned_stopwords]
            if not kept:
                tf_per_doc[path] = Counter()
                continue
            tf_per_doc[path] = Counter(kept)
            df.update(set(kept))

        # Pass 2: compute tf-idf vectors and norms
        idf = {term: math.log((n + 1) / (count + 1)) + 1.0 for term, count in df.items()}
        docs: dict[str, dict[str, float]] = {}
        norms: dict[str, float] = {}
        inverted: dict[str, list[str]] = defaultdict(list)
        for path, tf in tf_per_doc.items():
            if not tf:
                docs[path] = {}
                norms[path] = 0.0
                continue
            max_tf = max(tf.values())
            vec: dict[str, float] = {}
            for term, freq in tf.items():
                # Sub-linear TF + IDF
                w = (0.5 + 0.5 * freq / max_tf) * idf[term]
                vec[term] = w
                inverted[term].append(path)
            norm = math.sqrt(sum(w * w for w in vec.values()))
            docs[path] = vec
            norms[path] = norm

        return cls(docs=docs, norms=norms, inverted=dict(inverted), df=dict(df), n_docs=n)

    def top_k(self, src_path: str, k: int = 10, min_score: float = 0.05) -> list[tuple[str, float]]:
        """Return up to k candidate paths by cosine similarity, descending."""
        vec = self.docs.get(src_path)
        if not vec:
            return []
        src_norm = self.norms.get(src_path, 0.0)
        if src_norm == 0:
            return []

        # Accumulate scores via inverted index — only docs sharing >=1 term
        scores: dict[str, float] = defaultdict(float)
        for term, w in vec.items():
            for other in self.inverted.get(term, ()):
                if other == src_path:
                    continue
                ow = self.docs[other].get(term, 0.0)
                scores[other] += w * ow

        # Normalize to cosine and threshold
        ranked: list[tuple[str, float]] = []
        for other, dot in scores.items():
            on = self.norms.get(other, 0.0)
            if on == 0:
                continue
            sim = dot / (src_norm * on)
            if sim >= min_score:
                ranked.append((other, sim))
        ranked.sort(key=lambda x: -x[1])
        return ranked[:k]
