"""Helpers for emitting Dataview-friendly typed wikilinks."""
from __future__ import annotations

import re

from .ontology import Ontology
from .shapes import classify_note
from .walker import Note

_FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?", re.DOTALL)
_HEADING_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def format_typed_edge(predicate: str, link_target: str) -> str:
    """Render a Dataview inline property for a typed edge."""
    return f"{predicate}:: [[{link_target}]]"


def insert_typed_edge(text: str, predicate: str, link_target: str) -> tuple[str, bool]:
    """Insert a typed edge after frontmatter or before the first heading."""
    line = format_typed_edge(predicate, link_target)
    if line in text:
        return text, False

    if not text.strip():
        return line + "\n", True

    frontmatter = _FRONTMATTER_RE.match(text)
    if frontmatter:
        return _splice_insert(text, frontmatter.end(), line), True

    first_heading = _HEADING_RE.search(text)
    if first_heading:
        return _splice_insert(text, first_heading.start(), line), True

    return line + "\n\n" + text.lstrip("\n"), True


def infer_note_type(note: Note, ontology: Ontology, *, hub_paths: set[str] | None = None) -> str:
    """Best-effort type inference aligned to the composed ontology."""
    explicit = str(note.frontmatter.get("type") or note.frontmatter.get("kind") or "").strip().lower()
    if explicit and explicit in ontology.node_types:
        return explicit

    if hub_paths and note.relpath in hub_paths and "hub" in ontology.node_types:
        return "hub"

    inferred = classify_note(note)
    if inferred in ontology.node_types:
        return inferred
    if inferred == "generic":
        return "note"
    return "note" if "note" in ontology.node_types else inferred


def _splice_insert(text: str, offset: int, line: str) -> str:
    before = text[:offset].rstrip("\n")
    after = text[offset:].lstrip("\n")

    parts = [before, line]
    if after:
        parts.append(after)
    return "\n\n".join(part for part in parts if part) + "\n"
