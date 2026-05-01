"""Extract deadlines and date references from note text.

Catches:
  - ISO: 2026-06-15, 2026/06/15
  - "by Friday", "due Monday"
  - "end of Q2", "end of June"
  - "next week", "next month"
  - Dates following deadline keywords (deadline, due, by, before, ship by)

Relative references are resolved against the note's date if available
(passed as `note_date`), otherwise left as a relative phrase.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

_DEADLINE_KEYWORDS = r"(?:deadline|due|by|before|ship\s+by|target|cutoff|launch)"
_ISO_DATE_RE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_REL_DAY_RE = re.compile(
    r"\b(?:by|due|before)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next\s+week|next\s+month|end\s+of\s+(?:week|month|quarter|year))\b",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(
    r"\b(?:end\s+of\s+)?Q([1-4])(?:\s+(\d{4}))?\b",
    re.IGNORECASE,
)
_DEADLINE_CONTEXT_RE = re.compile(
    rf"\b{_DEADLINE_KEYWORDS}\b[^.!?\n]{{0,80}}",
    re.IGNORECASE,
)

_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


@dataclass
class Deadline:
    date_iso: str | None      # ISO date if resolvable, else None
    raw: str                   # what was matched ("by Friday", "2026-06-15")
    what: str                  # the surrounding context phrase
    quote: str                 # full surrounding sentence


def _surrounding_sentence(text: str, span: tuple[int, int]) -> str:
    start, end = span
    left = max(0, text.rfind(".", 0, start), text.rfind("!", 0, start),
               text.rfind("?", 0, start), text.rfind("\n", 0, start))
    right_candidates = [
        text.find(".", end), text.find("!", end), text.find("?", end), text.find("\n", end),
    ]
    right = min((c for c in right_candidates if c != -1), default=len(text))
    return text[left:right].strip(" \t.!?\n")


def _next_weekday(today: date, target: int) -> date:
    days = (target - today.weekday()) % 7
    if days == 0:
        days = 7
    return today + timedelta(days=days)


def _resolve_relative(phrase: str, note_date: date | None) -> str | None:
    if note_date is None:
        return None
    p = phrase.lower().strip()
    if p == "tomorrow":
        return (note_date + timedelta(days=1)).isoformat()
    if p == "next week":
        return (note_date + timedelta(days=7)).isoformat()
    if p == "next month":
        try:
            month = note_date.month + 1
            year = note_date.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            return date(year, month, min(note_date.day, 28)).isoformat()
        except ValueError:
            return None
    if p in _WEEKDAYS:
        return _next_weekday(note_date, _WEEKDAYS[p]).isoformat()
    if p.startswith("end of "):
        rest = p.replace("end of ", "")
        if rest == "week":
            return _next_weekday(note_date, 4).isoformat()  # Friday
        if rest == "month":
            month, year = note_date.month, note_date.year
            if month == 12:
                next_first = date(year + 1, 1, 1)
            else:
                next_first = date(year, month + 1, 1)
            return (next_first - timedelta(days=1)).isoformat()
        if rest == "quarter":
            q = (note_date.month - 1) // 3
            month_end = (q + 1) * 3
            try:
                next_q_first = date(note_date.year, month_end + 1, 1) if month_end < 12 else date(note_date.year + 1, 1, 1)
            except ValueError:
                return None
            return (next_q_first - timedelta(days=1)).isoformat()
    return None


def find_deadlines(text: str, note_date: date | None = None) -> list[Deadline]:
    out: list[Deadline] = []
    seen: set[tuple[int, int]] = set()

    for m in _ISO_DATE_RE.finditer(text):
        if m.span() in seen:
            continue
        seen.add(m.span())
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        sent = _surrounding_sentence(text, m.span())
        # Only flag as a deadline if the sentence has deadline language;
        # otherwise it might just be a date reference (e.g. meeting date).
        is_deadline = bool(re.search(_DEADLINE_KEYWORDS, sent, re.IGNORECASE))
        if is_deadline:
            out.append(Deadline(
                date_iso=d.isoformat(),
                raw=m.group(0),
                what=sent[:140],
                quote=sent,
            ))

    for m in _REL_DAY_RE.finditer(text):
        if m.span() in seen:
            continue
        seen.add(m.span())
        phrase = m.group(1).lower()
        resolved = _resolve_relative(phrase, note_date)
        sent = _surrounding_sentence(text, m.span())
        out.append(Deadline(
            date_iso=resolved,
            raw=m.group(0),
            what=sent[:140],
            quote=sent,
        ))

    return out
