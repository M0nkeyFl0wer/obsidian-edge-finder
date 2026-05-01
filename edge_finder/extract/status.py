"""Infer note lifecycle status: idea / active / shipped / blocked / abandoned.

Heuristics in priority order:
  1. Frontmatter `status:` field (authoritative)
  2. Frontmatter `tags:` containing #idea / #shipped / #abandoned / #blocked
  3. Folder hints (Ideas/, Active/, Archive/, Shipped/)
  4. In-body tags
  5. Default: "active" for projects, "unknown" for everything else
"""
from __future__ import annotations

from ..walker import Note

_VALID_STATUSES = {"idea", "active", "shipped", "blocked", "abandoned"}

_TAG_TO_STATUS = {
    "idea": "idea",
    "ideas": "idea",
    "wip": "active",
    "in-progress": "active",
    "active": "active",
    "shipped": "shipped",
    "done": "shipped",
    "complete": "shipped",
    "completed": "shipped",
    "blocked": "blocked",
    "stuck": "blocked",
    "abandoned": "abandoned",
    "archived": "abandoned",
}

_FOLDER_TO_STATUS = {
    "ideas": "idea",
    "inbox": "idea",
    "active": "active",
    "in-progress": "active",
    "wip": "active",
    "shipped": "shipped",
    "completed": "shipped",
    "archive": "abandoned",
    "archived": "abandoned",
}


def _normalize_tag(tag: str) -> str:
    return tag.strip().lstrip("#").lower()


def infer_status(note: Note, note_type: str) -> str:
    fm_status = (note.frontmatter.get("status") or "").strip().lower()
    if fm_status in _VALID_STATUSES:
        return fm_status

    for tag in note.tags:
        normalized = _normalize_tag(tag)
        if normalized in _TAG_TO_STATUS:
            return _TAG_TO_STATUS[normalized]

    for part in note.path.parts:
        low = part.lower()
        if low in _FOLDER_TO_STATUS:
            return _FOLDER_TO_STATUS[low]

    if note_type == "project":
        return "active"
    return "unknown"
