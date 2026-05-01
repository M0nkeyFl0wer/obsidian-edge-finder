"""Note dates, kept honest about which signal each one comes from.

Three different functions because they answer different questions:
  - `age_days`     — how long since this file was touched (mtime). Useful
                     for "stale active project" — a project nobody has even
                     opened in 90 days is probably actually stale.
  - `content_date` — when the note was AUTHORED, per frontmatter or filename.
                     Returns None when there's no authored signal. Used by
                     activity-burst and similar "what's new" detectors,
                     where mtime fallback would lie about recency on
                     bulk-imported / synced notes.
  - `note_date`    — best-effort date with mtime fallback. Used only for
                     deadline-resolution (relative-date math anchor) where
                     SOME date is better than none.
"""
from __future__ import annotations

from datetime import date, datetime

from ..walker import Note


def age_days(note: Note, today: date | None = None) -> int:
    today = today or date.today()
    mtime = datetime.fromtimestamp(note.path.stat().st_mtime).date()
    return max(0, (today - mtime).days)


def _try_filename_date(stem: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y_%m_%d", "%Y%m%d"):
        try:
            sample = stem[:10] if "-" in stem or "_" in stem else stem[:8]
            return datetime.strptime(sample, fmt).date()
        except (ValueError, IndexError):
            continue
    return None


def content_date(note: Note) -> date | None:
    """Authored date. None if no frontmatter date and no filename date prefix.

    Does NOT fall back to mtime. mtime is a filesystem fact, not a content
    fact: bulk-imports, sync tools, plugin operations, and file moves all
    update mtime without anyone authoring anything. Detectors that ask
    "what's new in the user's actual work" must not be fooled by that.
    """
    fm_date = note.frontmatter.get("date")
    if isinstance(fm_date, str):
        try:
            return date.fromisoformat(fm_date[:10])
        except ValueError:
            pass
    return _try_filename_date(note.path.stem)


def note_date(note: Note) -> date | None:
    """Best-effort date with mtime fallback — for deadline anchoring only.

    Use `content_date` for any analytic that talks about "recent activity".
    """
    cd = content_date(note)
    if cd is not None:
        return cd
    try:
        return datetime.fromtimestamp(note.path.stat().st_mtime).date()
    except OSError:
        return None
