"""Detect note shapes (Granola meeting, daily note, project page, person page, generic).

Heuristics only — no LLM. The point is to give the user a starting ontology
they can edit, not to be authoritative. When in doubt, fall back to "generic".
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .walker import Note

GRANOLA_HEADINGS = {"summary", "attendees", "notes", "action items", "transcript"}
DATE_FILENAME_RE = re.compile(r"^(?:\d{4}[-_/]?\d{2}[-_/]?\d{2})")
# Folder hints are matched case-insensitively as substrings of any path part,
# so "03_Projects" / "01-People" / "_Daily Notes" all work without exact match.
PERSON_FOLDER_HINTS = ("people", "contacts", "folks")
PROJECT_FOLDER_HINTS = ("project", "initiative")
DAILY_FOLDER_HINTS = ("daily", "journal")


def _folder_hit(parts: tuple[str, ...], hints: tuple[str, ...]) -> bool:
    return any(any(h in p.lower() for h in hints) for p in parts)


@dataclass
class ShapeReport:
    by_type: Counter
    granola_share: float
    has_attendees_section: int
    has_action_items_section: int
    date_prefixed: int
    in_projects_folder: int
    in_people_folder: int
    in_daily_folder: int


def classify_note(note: Note) -> str:
    """Return a single inferred type for this note."""
    fm_type = (note.frontmatter.get("type") or note.frontmatter.get("kind") or "").strip().lower()
    if fm_type in {"meeting", "daily", "person", "project", "topic", "decision"}:
        return fm_type

    parts = tuple(note.path.parts)
    if _folder_hit(parts, PERSON_FOLDER_HINTS):
        return "person"
    if _folder_hit(parts, PROJECT_FOLDER_HINTS):
        return "project"
    if _folder_hit(parts, DAILY_FOLDER_HINTS):
        return "daily"

    heading_set = {h.lower() for _, h in note.headings}
    granola_hits = len(heading_set & GRANOLA_HEADINGS)
    if granola_hits >= 2:
        return "meeting"
    if DATE_FILENAME_RE.match(note.path.stem):
        if "attendees" in heading_set or "summary" in heading_set:
            return "meeting"
        return "daily"
    if "attendees" in heading_set or "action items" in heading_set:
        return "meeting"

    return "generic"


def summarize_shapes(notes: list[Note]) -> tuple[dict[str, str], ShapeReport]:
    """Classify all notes and return (path → type, vault-wide report)."""
    by_path: dict[str, str] = {}
    by_type: Counter = Counter()
    granola_count = 0
    has_attendees = 0
    has_action_items = 0
    date_prefixed = 0
    in_projects = 0
    in_people = 0
    in_daily = 0

    for note in notes:
        t = classify_note(note)
        by_path[note.relpath] = t
        by_type[t] += 1

        heading_set = {h.lower() for _, h in note.headings}
        if len(heading_set & GRANOLA_HEADINGS) >= 2:
            granola_count += 1
        if "attendees" in heading_set:
            has_attendees += 1
        if "action items" in heading_set:
            has_action_items += 1
        if DATE_FILENAME_RE.match(note.path.stem):
            date_prefixed += 1
        parts = tuple(note.path.parts)
        if _folder_hit(parts, PROJECT_FOLDER_HINTS):
            in_projects += 1
        if _folder_hit(parts, PERSON_FOLDER_HINTS):
            in_people += 1
        if _folder_hit(parts, DAILY_FOLDER_HINTS):
            in_daily += 1

    granola_share = granola_count / len(notes) if notes else 0.0
    report = ShapeReport(
        by_type=by_type,
        granola_share=granola_share,
        has_attendees_section=has_attendees,
        has_action_items_section=has_action_items,
        date_prefixed=date_prefixed,
        in_projects_folder=in_projects,
        in_people_folder=in_people,
        in_daily_folder=in_daily,
    )
    return by_path, report


def draft_ontology(report: ShapeReport) -> dict:
    """Propose node types and edge types based on what we saw in the vault."""
    node_types: list[str] = []
    if report.by_type.get("meeting", 0) > 0:
        node_types.append("meeting")
    if report.by_type.get("project", 0) > 0 or report.in_projects_folder > 0:
        node_types.append("project")
    if report.by_type.get("person", 0) > 0 or report.has_attendees_section > 0:
        node_types.append("person")
    if report.by_type.get("daily", 0) > 0:
        node_types.append("daily")
    node_types.append("topic")

    edge_types: list[dict] = []
    if "meeting" in node_types and "project" in node_types:
        edge_types.append({"name": "discusses", "from": "meeting", "to": "project"})
    if "meeting" in node_types and "person" in node_types:
        edge_types.append({"name": "attended_by", "from": "meeting", "to": "person"})
    if "meeting" in node_types:
        edge_types.append({"name": "follows_up_on", "from": "meeting", "to": "meeting"})
    if "project" in node_types and "person" in node_types:
        edge_types.append({"name": "owned_by", "from": "project", "to": "person"})
    edge_types.append({"name": "related_to", "from": "*", "to": "*"})

    return {"node_types": node_types, "edge_types": edge_types}
