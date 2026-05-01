"""Detect and classify stub notes — structured-but-empty notes that confuse
the rest of edge-finder because there's nothing semantic to match on.

A stub is a note with structured intent (frontmatter URL, type, tags) but
no body content, no outgoing wikilinks, and below a body-word-count
threshold. Stub *corpora* are folders where stubs dominate — typically
the result of a bulk import (Pocket, Raindrop, Instapaper, browser
bookmarks, Notion exports).

This module is read-only — it classifies notes but doesn't mutate them.
The mutation step (hub-attach) lives in triage.py.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .walker import Note

# Heuristic thresholds. Tunable via the public API but the defaults are
# what the test suite locks in.
DEFAULT_MIN_WORDS = 50
DEFAULT_MIN_CORPUS_SIZE = 50
DEFAULT_MIN_CORPUS_SHARE = 0.80

# Frontmatter keys whose presence (with a non-empty value) is a structured
# signal that this note has *intent* even if the body is empty.
_URL_KEYS = ("url", "source", "link", "href")
_BOOKMARK_TYPES = {"bookmark", "reference", "stub", "contact", "person", "clipping"}

_URL_BODY_RE = re.compile(r"\*\*(?:URL|Source|Link)\*\*:|\bhttps?://", re.IGNORECASE)
_DAILY_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:-\d{4})?$")


@dataclass
class StubInfo:
    """A single note that was classified as a stub."""
    relpath: str
    folder: str
    classification: str          # "url" | "person" | "topic" | "project" | "daily" | "unknown"
    body_word_count: int
    has_url: bool
    has_url_frontmatter: bool
    fm_type: str = ""
    tags: list[str] = field(default_factory=list)
    candidate_hub_tags: list[str] = field(default_factory=list)  # tags that look like hub names


@dataclass
class StubCorpus:
    """A folder where stubs dominate — likely a bulk-import artifact."""
    folder: str
    n_stubs: int
    n_total: int
    classifications: Counter
    sample: list[str]
    signature: str               # human-readable description of the pattern
    mean_body_words: int

    @property
    def share(self) -> float:
        return self.n_stubs / self.n_total if self.n_total else 0.0


def _folder_of(relpath: str) -> str:
    """Top-level folder of a relpath, or '<root>' for root-level files."""
    p = PurePosixPath(relpath.replace("\\", "/"))
    if len(p.parts) <= 1:
        return "<root>"
    return p.parts[0]


def _has_url_in_frontmatter(fm: dict) -> bool:
    for k in _URL_KEYS:
        v = fm.get(k)
        if isinstance(v, str) and v.strip().startswith(("http://", "https://")):
            return True
        if isinstance(v, list) and any(
            isinstance(x, str) and x.strip().startswith(("http://", "https://"))
            for x in v
        ):
            return True
    return False


def _has_url_in_body(body: str) -> bool:
    # Scan only the first ~2KB to avoid quadratic regex on large notes.
    return bool(_URL_BODY_RE.search(body[:2048]))


def _fm_type(fm: dict) -> str:
    t = fm.get("type")
    if isinstance(t, str):
        return t.strip().lower()
    return ""


def is_stub(note: Note, *, min_words: int = DEFAULT_MIN_WORDS) -> bool:
    """Return True if `note` looks like a stub (no body, no edges, structured intent)."""
    if note.wikilinks:
        return False  # has at least one outgoing edge → not a stub
    if note.word_count >= min_words:
        return False  # has substantive body → not a stub
    has_url_fm = _has_url_in_frontmatter(note.frontmatter)
    has_url_body = _has_url_in_body(note.body)
    fm_type = _fm_type(note.frontmatter)
    has_structured_signal = (
        has_url_fm
        or has_url_body
        or (fm_type in _BOOKMARK_TYPES)
        or (note.frontmatter.get("source") in ("pocket", "firefox", "flipboard", "raindrop", "instapaper"))
    )
    if not has_structured_signal:
        # A short note with no URL and no bookmark-shaped type — could just be
        # a thin draft. Don't flag it as a stub.
        return False
    return True


def classify(note: Note) -> str:
    """Return a stub classification label for `note`. Caller should have
    already confirmed `is_stub(note)`; this just dispatches the type."""
    fm = note.frontmatter
    fm_type = _fm_type(fm)

    if fm_type == "person" or "person" in note.tags or note.relpath.startswith("people/"):
        return "person"
    if fm_type == "project" or note.relpath.startswith(("Projects/", "03_Projects/")):
        return "project"
    if _DAILY_NAME_RE.match(PurePosixPath(note.relpath).stem):
        return "daily"
    if _has_url_in_frontmatter(fm) or _has_url_in_body(note.body):
        return "url"
    # has tags but no URL → likely a tag-only topic stub
    if note.tags:
        return "topic"
    return "unknown"


def _signature_for(folder: str, classifications: Counter, sample_paths: list[str]) -> str:
    """A short human-readable description of the import pattern."""
    if classifications.get("url", 0) >= 0.9 * sum(classifications.values()):
        # Look for the import-source clue
        return "URL-stub corpus (likely Pocket/Firefox/Flipboard/Raindrop import)"
    if classifications.get("person", 0) >= 0.9 * sum(classifications.values()):
        return "Person-stub corpus (likely contact import)"
    if classifications.get("daily", 0) >= 0.9 * sum(classifications.values()):
        return "Daily-stub corpus (auto-created daily notes never filled in)"
    return "Mixed stub corpus"


def find_stubs(notes: list[Note], *, min_words: int = DEFAULT_MIN_WORDS) -> list[StubInfo]:
    """Walk the notes and return one StubInfo per detected stub."""
    out: list[StubInfo] = []
    for n in notes:
        if not is_stub(n, min_words=min_words):
            continue
        out.append(StubInfo(
            relpath=n.relpath,
            folder=_folder_of(n.relpath),
            classification=classify(n),
            body_word_count=n.word_count,
            has_url=_has_url_in_body(n.body),
            has_url_frontmatter=_has_url_in_frontmatter(n.frontmatter),
            fm_type=_fm_type(n.frontmatter),
            tags=list(n.tags),
            candidate_hub_tags=[t for t in n.tags if not t.startswith(("bookmark", "stub", "clipping"))],
        ))
    return out


def detect_stub_corpora(
    notes: list[Note],
    *,
    min_words: int = DEFAULT_MIN_WORDS,
    min_corpus_size: int = DEFAULT_MIN_CORPUS_SIZE,
    min_corpus_share: float = DEFAULT_MIN_CORPUS_SHARE,
) -> tuple[list[StubCorpus], list[StubInfo]]:
    """Return (corpora, all_stubs) for the vault.

    A folder is reported as a corpus when:
      - it contains at least `min_corpus_size` notes total, AND
      - at least `min_corpus_share` of those notes are stubs.

    The full list of all stub notes is returned alongside the corpora so
    triage.py can act on individual stubs in non-corpus folders too.
    """
    stubs = find_stubs(notes, min_words=min_words)
    stubs_by_folder: dict[str, list[StubInfo]] = defaultdict(list)
    for s in stubs:
        stubs_by_folder[s.folder].append(s)

    notes_by_folder: dict[str, int] = defaultdict(int)
    body_words_by_folder: dict[str, list[int]] = defaultdict(list)
    for n in notes:
        f = _folder_of(n.relpath)
        notes_by_folder[f] += 1
        if f in stubs_by_folder:  # only track word-counts where it'll matter
            body_words_by_folder[f].append(n.word_count)

    corpora: list[StubCorpus] = []
    for folder, folder_stubs in stubs_by_folder.items():
        n_total = notes_by_folder[folder]
        n_stubs = len(folder_stubs)
        if n_total < min_corpus_size or n_stubs / n_total < min_corpus_share:
            continue
        classifications = Counter(s.classification for s in folder_stubs)
        sample = [s.relpath for s in folder_stubs[:3]]
        bw = body_words_by_folder.get(folder, [])
        mean_body_words = int(sum(bw) / len(bw)) if bw else 0
        corpora.append(StubCorpus(
            folder=folder,
            n_stubs=n_stubs,
            n_total=n_total,
            classifications=classifications,
            sample=sample,
            signature=_signature_for(folder, classifications, sample),
            mean_body_words=mean_body_words,
        ))

    # Sort biggest corpus first; that's what users want to see.
    corpora.sort(key=lambda c: -c.n_stubs)
    return corpora, stubs
