"""Surprise detectors — statistical outliers worth surfacing.

These run during scan and feed a "What jumped out" section at the top of
vault-report.md. No LLM, no embeddings — just distributions over data we
already extract.

Each detector returns a list of `Surprise` records or an empty list. The
report renderer keeps the top N across all detectors, ranked by `weight`
(detector-defined: how interesting this finding is, on a rough 0..10 scale).
"""
from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import networkx as nx

from .fingerprint import Fingerprint
from .walker import Note


@dataclass
class Surprise:
    kind: str            # "money-outlier" | "folder-mismatch" | etc.
    headline: str        # one-line plain-English finding
    weight: float        # higher = more interesting; rough 0..10 scale
    detail: str = ""     # optional second line


# ---------- Detectors ----------

def detect_money_outliers(fps: list[Fingerprint]) -> list[Surprise]:
    amounts: list[tuple[Fingerprint, float, str]] = []
    for fp in fps:
        for m in fp.money:
            if m.amount_normalized and m.amount_normalized > 0:
                amounts.append((fp, m.amount_normalized, m.raw))
    if len(amounts) < 5:
        return []
    # Use a high quantile (top 10%) as the "noteworthy" floor rather than median.
    # Median is dominated by tiny mentions ($20 bill, $700 boat) and produces
    # misleading multipliers. Top-decile is what the user actually cares about.
    values = sorted(a[1] for a in amounts)
    p90 = values[int(len(values) * 0.9)]
    out: list[Surprise] = []
    seen_paths: set[str] = set()
    for fp, amt, raw in sorted(amounts, key=lambda x: -x[1]):
        if amt < p90:
            break
        if fp.path in seen_paths:
            continue
        seen_paths.add(fp.path)
        out.append(Surprise(
            kind="money-outlier",
            headline=f"`{fp.path}` mentions {raw} — among the largest money mentions in your vault",
            weight=min(8.0, 4.0 + min(amt / 1_000_000, 4.0)),
        ))
        if len(out) >= 3:
            break
    return out


def detect_folder_content_mismatch(fps: list[Fingerprint]) -> list[Surprise]:
    out: list[Surprise] = []
    for fp in fps:
        # Person folder note that mostly talks about projects
        if fp.type == "person" and len(fp.projects_mentioned) >= 3:
            out.append(Surprise(
                kind="folder-mismatch",
                headline=f"`{fp.path}` lives in a person folder but mentions {len(fp.projects_mentioned)} projects — could be mis-filed or a project lead's hub note",
                weight=5.0,
            ))
        # Project folder note with no project signal at all
        elif fp.type == "project" and not fp.projects_mentioned and not fp.stack and not fp.summary:
            out.append(Surprise(
                kind="folder-mismatch",
                headline=f"`{fp.path}` is in a project folder but has no project-shaped content (no mentions, no stack, no summary)",
                weight=4.0,
            ))
    return sorted(out, key=lambda s: -s.weight)[:3]


def detect_activity_bursts(fps: list[Fingerprint], today: date | None = None) -> list[Surprise]:
    today = today or date.today()
    recent_cutoff = today - timedelta(days=14)
    prior_start = today - timedelta(days=44)
    prior_end = recent_cutoff

    recent_tags: Counter = Counter()
    prior_tags: Counter = Counter()
    for fp in fps:
        # Use content_date, NOT note_date — mtime-derived dates lie on
        # bulk-imported / synced web-clips (every clipped article gets the
        # sync day's mtime, producing fake "burst" findings). If we can't
        # tell when a note was authored, we don't count it for activity.
        if not fp.content_date:
            continue
        try:
            d = date.fromisoformat(fp.content_date)
        except ValueError:
            continue
        if d >= recent_cutoff:
            recent_tags.update(fp.topics)
            recent_tags.update(fp.projects_mentioned)
        elif prior_start <= d < prior_end:
            prior_tags.update(fp.topics)
            prior_tags.update(fp.projects_mentioned)

    out: list[Surprise] = []
    for tag, recent_count in recent_tags.most_common():
        if recent_count < 3:
            break
        prior_count = prior_tags.get(tag, 0)
        if prior_count == 0 and recent_count >= 3:
            out.append(Surprise(
                kind="activity-burst",
                headline=f"`{tag}` has appeared in {recent_count} notes in the last 14 days — wasn't on your radar at all in the prior month",
                weight=7.0,
            ))
        elif prior_count > 0 and recent_count >= prior_count * 2:
            out.append(Surprise(
                kind="activity-burst",
                headline=f"`{tag}` is heating up — {recent_count} notes in 14 days vs {prior_count} in the 30 days before",
                weight=6.0,
            ))
    return out[:3]


_TEMPLATE_PATH_HINTS = ("template", "templates", "meta", "_templates")
_PLACEHOLDER_ASSIGNEES = {"person", "name", "assignee", "someone", "you", "me"}


def _looks_like_template(path: str) -> bool:
    parts = path.lower().replace("\\", "/").split("/")
    return any(p in _TEMPLATE_PATH_HINTS for p in parts)


def detect_stalled_action_items(fps: list[Fingerprint], today: date | None = None) -> list[Surprise]:
    today = today or date.today()
    out: list[Surprise] = []
    for fp in fps:
        if not fp.action_items or fp.age_days < 30:
            continue
        # Templates have placeholder action items that aren't real work.
        if _looks_like_template(fp.path):
            continue
        for ai in fp.action_items:
            if not ai.assignee:
                continue
            # Skip placeholder assignees ("@person", "@name", etc.)
            if ai.assignee.lower() in _PLACEHOLDER_ASSIGNEES:
                continue
            out.append(Surprise(
                kind="stalled-action-item",
                headline=f"@{ai.assignee} has an open action item from `{fp.path}` ({fp.age_days} days old)",
                weight=min(8.0, 3.0 + fp.age_days / 30),
                detail=ai.text[:120],
            ))
    return sorted(out, key=lambda s: -s.weight)[:3]


def detect_asymmetric_bridges(g: nx.Graph) -> list[Surprise]:
    out: list[Surprise] = []
    for node in nx.articulation_points(g):
        # Test what happens when we remove it
        h = g.copy()
        h.remove_node(node)
        comps = sorted((len(c) for c in nx.connected_components(h)), reverse=True)
        if len(comps) < 2:
            continue
        biggest, smallest = comps[0], comps[-1]
        if smallest >= 2 and biggest >= 5 * smallest and biggest >= 20:
            out.append(Surprise(
                kind="asymmetric-bridge",
                headline=f"`{node}` is the only thing keeping a {smallest}-note cluster connected to a {biggest}-note cluster",
                weight=min(8.0, 4.0 + biggest / 50),
            ))
    return sorted(out, key=lambda s: -s.weight)[:3]


def detect_ghost_active_projects(fps: list[Fingerprint], today: date | None = None) -> list[Surprise]:
    """Active-status project notes that nothing recent mentions."""
    today = today or date.today()
    recent_cutoff = today - timedelta(days=30)

    # Set of project names mentioned in any recent note (authored-date based)
    recent_mentions: set[str] = set()
    for fp in fps:
        if not fp.content_date:
            continue
        try:
            d = date.fromisoformat(fp.content_date)
        except ValueError:
            continue
        if d >= recent_cutoff:
            recent_mentions.update(fp.projects_mentioned)

    out: list[Surprise] = []
    for fp in fps:
        if fp.type != "project" or fp.status != "active":
            continue
        # Match the project's stem against recent mentions
        stem = Path(fp.path).stem
        if stem in recent_mentions:
            continue
        # Or its title — get from path
        if fp.age_days > 30:
            out.append(Surprise(
                kind="ghost-active-project",
                headline=f"`{fp.path}` is marked active but hasn't been touched in {fp.age_days} days — and nothing recent mentions it",
                weight=min(7.0, 3.0 + fp.age_days / 60),
            ))
    return sorted(out, key=lambda s: -s.weight)[:3]


def detect_cross_stack_outliers(fps: list[Fingerprint]) -> list[Surprise]:
    """Notes that mention 3+ very different tech stacks together."""
    # Compute per-tech document frequency
    tech_df: Counter = Counter()
    for fp in fps:
        for s in fp.stack:
            tech_df[s.name] += 1

    out: list[Surprise] = []
    for fp in fps:
        if len(fp.stack) < 3:
            continue
        # Average DF of this note's tech — lower means more unusual combination
        avg_df = statistics.mean(tech_df[s.name] for s in fp.stack)
        if avg_df > 30:  # Common combos aren't surprising
            continue
        names = [s.name for s in fp.stack[:5]]
        out.append(Surprise(
            kind="cross-stack",
            headline=f"`{fp.path}` mixes {len(fp.stack)} tech stacks ({', '.join(names)}) — unusual combination for this vault",
            weight=min(6.0, 2.0 + len(fp.stack) / 2),
        ))
    return sorted(out, key=lambda s: -s.weight)[:3]


# ---------- Aggregator ----------

def find_surprises(
    notes: list[Note],
    fps: list[Fingerprint],
    g: nx.Graph,
    *,
    max_total: int = 7,
) -> list[Surprise]:
    detectors = [
        detect_money_outliers(fps),
        detect_folder_content_mismatch(fps),
        detect_activity_bursts(fps),
        detect_stalled_action_items(fps),
        detect_asymmetric_bridges(g),
        detect_ghost_active_projects(fps),
        detect_cross_stack_outliers(fps),
    ]
    all_surprises = [s for batch in detectors for s in batch]
    all_surprises.sort(key=lambda s: -s.weight)
    return all_surprises[:max_total]
