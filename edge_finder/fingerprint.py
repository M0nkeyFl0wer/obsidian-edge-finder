"""Per-note fingerprint: structured extract aligned to the ontology.

The fingerprint is the unit of work in the propose stage — it's what gets
matched against other fingerprints to find edges. Today most fields are
filled deterministically (regex / Granola-section parsing / wikilink
resolution). The fields the LLM will fill later (topics, themes, decisions
in non-Granola notes) are present but empty in the v0 fingerprint.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract.age import age_days, content_date, note_date
from .extract.dates import Deadline, find_deadlines
from .extract.granola import ActionItem, parse_granola
from .extract.money import MoneyMention, find_money
from .extract.stack import StackMention, find_stack
from .extract.status import infer_status
from .walker import Note

_DATE_PREFIX_RE = re.compile(r"^\d{4}[-_]?\d{2}[-_]?\d{2}")


@dataclass
class Fingerprint:
    path: str
    type: str
    status: str
    age_days: int
    note_date: str | None         # best-effort with mtime fallback (deadline anchor)
    content_date: str | None      # authored date only — None if no frontmatter/filename signal
    # Granola / structured-section extracts (deterministic when sections exist)
    attendees: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)         # bullet lines from ## Summary
    has_granola_shape: bool = False
    # Cross-reference extracts (from existing wikilinks)
    projects_mentioned: list[str] = field(default_factory=list)   # wikilinks to project-typed notes
    follow_ups: list[str] = field(default_factory=list)            # wikilinks whose target is date-prefixed
    # Cross-cutting dimensions
    money: list[MoneyMention] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    stack: list[StackMention] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)            # from #tags


def _classify_wikilinks(
    wikilinks: list[str],
    type_by_path: dict[str, str],
    title_to_path: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Bucket wikilinks into (projects_mentioned, follow_ups)."""
    projects: list[str] = []
    follow_ups: list[str] = []
    seen: set[str] = set()
    for raw in wikilinks:
        target = raw.strip()
        if not target or target in seen:
            continue
        seen.add(target)
        # Date-prefixed targets → follow_ups
        if _DATE_PREFIX_RE.match(target):
            follow_ups.append(target)
            continue
        # Resolve to path then to type
        path = title_to_path.get(target) or title_to_path.get(target.lower())
        if path:
            t = type_by_path.get(path, "generic")
            if t == "project":
                projects.append(target)
    return projects, follow_ups


def build_fingerprint(
    note: Note,
    note_type: str,
    *,
    type_by_path: dict[str, str] | None = None,
    title_to_path: dict[str, str] | None = None,
) -> Fingerprint:
    nd = note_date(note)
    cd = content_date(note)
    granola = parse_granola(note.body)
    type_by_path = type_by_path or {}
    title_to_path = title_to_path or {}

    projects, follow_ups = _classify_wikilinks(
        note.wikilinks, type_by_path, title_to_path,
    )

    return Fingerprint(
        path=note.relpath,
        type=note_type,
        status=infer_status(note, note_type),
        age_days=age_days(note),
        note_date=nd.isoformat() if nd else None,
        content_date=cd.isoformat() if cd else None,
        attendees=granola.attendees,
        action_items=granola.action_items,
        decisions=granola.decisions,
        summary=granola.summary_lines,
        has_granola_shape=granola.has_granola_shape,
        projects_mentioned=projects,
        follow_ups=follow_ups,
        money=find_money(note.body),
        deadlines=find_deadlines(note.body, nd),
        stack=find_stack(note),
        topics=list(note.tags),
    )


def fingerprint_completeness(fp: Fingerprint) -> tuple[int, int]:
    """Return (filled_fields, total_significant_fields).

    Used to estimate how much LLM enrichment a note will need. A note where
    most signal-bearing fields are already populated deterministically needs
    less LLM work.
    """
    fields_to_check = [
        bool(fp.attendees),
        bool(fp.action_items),
        bool(fp.decisions),
        bool(fp.summary),
        bool(fp.projects_mentioned),
        bool(fp.follow_ups),
        bool(fp.topics),
        bool(fp.money),
        bool(fp.deadlines),
        bool(fp.stack),
    ]
    return sum(fields_to_check), len(fields_to_check)
