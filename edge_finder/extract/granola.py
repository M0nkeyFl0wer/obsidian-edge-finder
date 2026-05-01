"""Parse Granola-shaped meeting notes into structured fields.

Granola exports notes with predictable sections:
  ## Summary       — AI-generated bullet summary
  ## Attendees     — bullet list of names
  ## Action Items  — bullet list, often with @assignee mentions
  ## Notes         — meeting notes / transcript-derived bullets
  ## Decisions     — sometimes present
  ## Transcript    — sometimes present, full transcript

We extract from the structured sections deterministically, no LLM. For
non-Granola notes the extractors return empty results gracefully.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECTION_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+?)\s*$", re.MULTILINE)
_ASSIGNEE_RE = re.compile(r"@([A-Za-z][\w-]+)")
_WIKILINK_RE = re.compile(r"\[\[([^\]\|\#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")


@dataclass
class ActionItem:
    text: str
    assignee: str | None = None
    quote: str = ""


@dataclass
class GranolaParse:
    summary_lines: list[str] = field(default_factory=list)
    attendees: list[str] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    has_granola_shape: bool = False


def _section_body(body: str, heading_pattern: str) -> str | None:
    """Extract the text under a section heading, up to the next heading of equal/higher level."""
    headings = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(headings):
        level, title = m.group(1), m.group(2).strip().lower()
        if not re.match(heading_pattern, title, re.IGNORECASE):
            continue
        start = m.end()
        # Find the next heading of equal-or-higher level (fewer #s)
        end = len(body)
        for n in headings[i+1:]:
            if len(n.group(1)) <= len(level):
                end = n.start()
                break
        return body[start:end].strip()
    return None


def _parse_attendees(section: str) -> list[str]:
    out: list[str] = []
    for m in _BULLET_RE.finditer(section):
        line = m.group(1).strip()
        # Strip role parenthesizing — "Alice (PM)" → "Alice"
        line = re.sub(r"\s*\([^)]*\)\s*$", "", line)
        # Strip wikilink syntax to get clean name
        wikilink = _WIKILINK_RE.match(line)
        if wikilink:
            line = wikilink.group(1)
        if line and len(line) < 60:
            out.append(line)
    return out


def _parse_action_items(section: str) -> list[ActionItem]:
    out: list[ActionItem] = []
    for m in _BULLET_RE.finditer(section):
        text = m.group(1).strip()
        if not text:
            continue
        assignee_match = _ASSIGNEE_RE.search(text)
        assignee = assignee_match.group(1) if assignee_match else None
        out.append(ActionItem(text=text[:200], assignee=assignee, quote=text[:240]))
    return out


def _parse_decisions(section: str) -> list[str]:
    return [m.group(1).strip()[:200] for m in _BULLET_RE.finditer(section) if m.group(1).strip()]


def parse_granola(body: str) -> GranolaParse:
    """Parse Granola-shaped sections out of a note body. Empty result for non-Granola notes."""
    result = GranolaParse()

    summary = _section_body(body, r"^summary$")
    if summary is not None:
        result.summary_lines = [
            m.group(1).strip() for m in _BULLET_RE.finditer(summary) if m.group(1).strip()
        ]
        if not result.summary_lines:
            # Sometimes the summary is plain prose, not bullets
            line = summary.split("\n\n", 1)[0].strip()
            if line:
                result.summary_lines = [line[:300]]

    attendees_section = _section_body(body, r"^attendees$")
    if attendees_section is not None:
        result.attendees = _parse_attendees(attendees_section)

    actions_section = _section_body(body, r"^(action items|action item|actions|todos|todo|tasks)$")
    if actions_section is not None:
        result.action_items = _parse_action_items(actions_section)

    decisions_section = _section_body(body, r"^(decisions|decided)$")
    if decisions_section is not None:
        result.decisions = _parse_decisions(decisions_section)

    # Granola signature: at least 2 of (Summary, Attendees, Notes/Action Items)
    signal = sum([
        bool(result.summary_lines),
        bool(result.attendees),
        bool(result.action_items),
    ])
    result.has_granola_shape = signal >= 2

    return result
