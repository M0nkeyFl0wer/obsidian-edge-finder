"""Render the human-readable vault-report.md."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import yaml

from .fingerprint import Fingerprint
from .shapes import ShapeReport
from .surprises import Surprise
from .topology import GapsReport, islands_summary
from .walker import Note


def _pct(num: int, denom: int) -> str:
    if denom == 0:
        return "0%"
    return f"{round(100 * num / denom)}%"


def _render_dimensions(fingerprints: list[Fingerprint]) -> str:
    """Render the cross-cutting dimensions sections (money, deadlines, stale, ideas, stack)."""
    parts: list[str] = []

    # 💰 Money
    money_fps = [fp for fp in fingerprints if fp.money]
    if money_fps:
        total_tracked = sum(
            (m.amount_normalized or 0)
            for fp in money_fps for m in fp.money
        )
        parts.append(f"### 💰 Money mentioned ({len(money_fps)} notes, ~${total_tracked:,.0f} tracked)\n")
        # Top 10 highest-value or most recent
        top = sorted(
            ((fp, m) for fp in money_fps for m in fp.money),
            key=lambda pair: -(pair[1].amount_normalized or 0),
        )[:10]
        for fp, m in top:
            amount_str = f"{m.raw}"
            parts.append(f"- **{amount_str}** ({m.currency}) — `{fp.path}`")
            quote = m.quote.replace("\n", " ")[:140]
            parts.append(f"  > {quote}")
        parts.append("")

    # ⏰ Upcoming deadlines (next 60 days)
    today = date.today()
    horizon = today + timedelta(days=60)
    upcoming: list[tuple[Fingerprint, object]] = []
    for fp in fingerprints:
        for d in fp.deadlines:
            if not d.date_iso:
                continue
            try:
                dd = date.fromisoformat(d.date_iso)
            except ValueError:
                continue
            if today <= dd <= horizon:
                upcoming.append((fp, d))
    upcoming.sort(key=lambda pair: pair[1].date_iso or "")
    if upcoming:
        parts.append(f"### ⏰ Upcoming deadlines ({len(upcoming)} in next 60 days)\n")
        for fp, d in upcoming[:15]:
            quote = d.quote.replace("\n", " ")[:120]
            parts.append(f"- **{d.date_iso}** — `{fp.path}`")
            parts.append(f"  > {quote}")
        parts.append("")

    # 🧊 Stale active projects
    stale = [
        fp for fp in fingerprints
        if fp.status == "active" and fp.age_days > 30 and fp.type == "project"
    ]
    if stale:
        stale.sort(key=lambda fp: -fp.age_days)
        parts.append(f"### 🧊 Stale active projects ({len(stale)} untouched > 30 days)\n")
        for fp in stale[:10]:
            parts.append(f"- `{fp.path}` (last touched {fp.age_days} days ago)")
        parts.append("")

    # 💡 Ideas without follow-up
    ideas = [fp for fp in fingerprints if fp.status == "idea"]
    if ideas:
        parts.append(f"### 💡 Ideas captured ({len(ideas)} notes)\n")
        for fp in ideas[:10]:
            parts.append(f"- `{fp.path}` ({fp.age_days}d old)")
        parts.append("")

    # 🛠️ Tech stack distribution
    tech_counter: Counter = Counter()
    for fp in fingerprints:
        for s in fp.stack:
            tech_counter[s.name] += 1
    if tech_counter:
        parts.append(f"### 🛠️ Tech stack across vault ({sum(tech_counter.values())} mentions)\n")
        for name, count in tech_counter.most_common(15):
            parts.append(f"- **{name}**: {count} note(s)")
        parts.append("")

    if not parts:
        return "_No money, deadlines, status signals, or tech stack detected in this vault._\n"
    return "\n".join(parts)


def _render_surprises(surprises: list[Surprise]) -> str:
    if not surprises:
        return ""
    parts = ["## ✨ What jumped out\n",
             "_Statistical outliers from the data we just extracted. Each one might be worth a glance before you dive into the details below._\n"]
    for s in surprises:
        parts.append(f"- {s.headline}")
        if s.detail:
            parts.append(f"  > {s.detail}")
    parts.append("\n---\n")
    return "\n".join(parts)


def _render_stub_corpora(stub_corpora: list, stub_orphan_overlap: int = 0) -> str:
    """Render the stub-corpora section of the gaps report.

    `stub_corpora` is a list of stubs.StubCorpus; typed loosely to avoid a
    circular import between report.py and stubs.py.
    """
    if not stub_corpora:
        return ""
    parts = [f"\n### 📌 Stub corpora detected ({len(stub_corpora)})\n"]
    parts.append(
        "These folders contain mostly URL-only or otherwise-empty notes — "
        "typically from a bulk import (Pocket, Raindrop, Instapaper, "
        "browser bookmarks, Notion exports). They confuse the rest of "
        "edge-finder because there's no body content for the matcher to "
        "read, so `propose --judge` correctly skips them. Triage them "
        "before running propose:\n"
    )
    parts.append("    edge-finder triage <vault>\n")
    for c in stub_corpora:
        breakdown = ", ".join(
            f"{n} {kind}" for kind, n in c.classifications.most_common()
        )
        parts.append(
            f"- **`{c.folder}/`** — {c.n_stubs} of {c.n_total} notes are "
            f"stubs ({round(100 * c.share)}%; mean body {c.mean_body_words} words). "
            f"Sample: " + ", ".join(f"`{p}`" for p in c.sample) + "."
        )
        parts.append(f"  - signature: _{c.signature}_")
        parts.append(f"  - classifications: {breakdown}")
    if stub_orphan_overlap:
        parts.append(
            f"\n_Note: {stub_orphan_overlap} of the orphans counted above "
            f"are inside these stub corpora. The real number of "
            f"connectable orphans is lower than the headline figure._"
        )
    parts.append("")
    return "\n".join(parts)


def render_report(
    vault_root: Path,
    notes: list[Note],
    by_path: dict[str, str],
    shape: ShapeReport,
    gaps: GapsReport,
    ontology: dict,
    fingerprints: list[Fingerprint] | None = None,
    surprises: list[Surprise] | None = None,
    stub_corpora: list | None = None,
    stub_orphan_overlap: int = 0,
) -> str:
    n = len(notes)
    type_counts = Counter(by_path.values())
    type_lines = "\n".join(
        f"- **{t}**: {c} ({_pct(c, n)})"
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1])
    )

    # Shape signature
    if shape.granola_share >= 0.30:
        shape_signature = (
            f"**Granola-shaped vault.** {round(100 * shape.granola_share)}% of "
            f"notes have the Granola template (Summary/Attendees/Notes "
            f"sections). Attendees and topics can be extracted from those "
            f"structured sections directly — no NER needed."
        )
    elif shape.has_attendees_section + shape.has_action_items_section >= max(3, n // 10):
        shape_signature = (
            "**Mixed meeting-note vault.** A meaningful share of notes have "
            "Attendees / Action Items sections, but not the full Granola "
            "template. We'll extract from sections where present and fall "
            "back to free-text scanning otherwise."
        )
    else:
        shape_signature = (
            "**Generic vault.** No dominant note template detected. The tool "
            "will rely on title-match, tags, and embedding similarity (later) "
            "to find connections."
        )

    # Gaps
    n_islands = gaps.n_islands
    n_orphans = gaps.n_orphans
    n_articulations = gaps.n_articulations
    n_loops = gaps.n_loops

    islands_md = ""
    if gaps.islands:
        biggest = gaps.islands[0]
        sample = biggest[:3]
        islands_md = (
            f"### 🏝️ Islands ({n_islands})\n\n"
            f"Your vault has **{n_islands} disconnected groups** of 2+ notes "
            f"that never reference each other ({islands_summary(gaps.islands)}). "
            f"The biggest island has {len(biggest)} notes; sample members:\n"
            + "\n".join(f"- `{p}`" for p in sample)
            + "\n\n→ A bridging index note that links across islands often unblocks browsing.\n"
        )
    if n_orphans:
        islands_md += (
            f"\n**{n_orphans} orphan notes** with zero links in either direction. "
            f"These are the most likely targets for new edges. Sample:\n"
            + "\n".join(f"- `{p}`" for p in gaps.orphans[:5])
            + "\n"
        )

    articulations_md = ""
    if gaps.articulation_points:
        articulations_md = (
            f"\n### 🌉 Load-bearing notes ({n_articulations})\n\n"
            "These notes hold otherwise-disconnected parts of your vault "
            "together. If you lost one, the graph fragments. Worth knowing "
            "which notes are doing this much work:\n\n"
            + "\n".join(f"- `{p}` (degree {d})" for p, d in gaps.articulation_points[:10])
            + "\n"
        )

    loops_md = ""
    if gaps.centerless_loops:
        loops_md = f"\n### ⭕ Conversations without a center ({n_loops})\n\n"
        loops_md += (
            "These groups of notes reference each other in a loop, but no single "
            "synthesis note pins down the through-line. Strong candidates for a "
            "new index/decision/summary note:\n\n"
        )
        for i, loop in enumerate(gaps.centerless_loops, 1):
            loops_md += f"**Loop {i}** ({len(loop)} notes):\n"
            loops_md += "\n".join(f"- `{p}`" for p in loop) + "\n\n"

    cocitations_md = ""
    if gaps.cocitations:
        cocitations_md = f"\n### 🔗 Co-cited but unlinked ({gaps.n_cocitations})\n\n"
        cocitations_md += (
            "These pairs of notes are cited together by multiple other notes, "
            "but never directly linked to each other. Often the cheapest, "
            "highest-confidence edges to add — third parties have already "
            "told you they belong together:\n\n"
        )
        for a, b, witness_count, witnesses in gaps.cocitations:
            cocitations_md += f"- `{a}` ↔ `{b}` (cited together by {witness_count} notes)\n"
            cocitations_md += "  witnesses: " + ", ".join(f"`{w}`" for w in witnesses) + "\n"
        cocitations_md += "\n"

    subclusters_md = ""
    if gaps.subclusters:
        subclusters_md = f"\n### 🧩 Sub-conversations inside your biggest island ({gaps.n_subclusters})\n\n"
        subclusters_md += (
            "Your largest connected component isn't really one conversation — "
            "it's several. Modularity detection found these sub-clusters that "
            "could each justify their own index note:\n\n"
        )
        for label, members in gaps.subclusters:
            subclusters_md += f"- **{label}** ({len(members)} notes); sample: "
            subclusters_md += ", ".join(f"`{m}`" for m in members[:3]) + "\n"
        subclusters_md += "\n"

    stub_corpora_md = _render_stub_corpora(stub_corpora or [], stub_orphan_overlap)

    if not (islands_md or articulations_md or loops_md or cocitations_md or subclusters_md or stub_corpora_md):
        gaps_md = "_No structural gaps detected — your existing wikilinks form a dense connected graph._\n"
    else:
        gaps_md = stub_corpora_md + islands_md + articulations_md + loops_md + cocitations_md + subclusters_md

    dimensions_md = _render_dimensions(fingerprints or [])
    surprises_md = _render_surprises(surprises or [])

    ontology_yaml = yaml.safe_dump(ontology, sort_keys=False, default_flow_style=False)

    return f"""# Vault Report — `{vault_root}`

_Generated by `edge-finder scan`. Read-only — no notes were modified._

{surprises_md}

## What's in your vault

- **{n} notes** total
- **{gaps.n_edges} existing wikilinks** between them (graph density: {gaps.n_edges / max(n, 1):.2f} per note)

### By inferred type

{type_lines}

### Shape signature

{shape_signature}

---

## Gaps

These come from looking at your vault as a network. The math is called
graph topology — but the answers are easier than the name. (If you want
to go deeper later, the same approach with embeddings can find
*topical* gaps too — concepts you almost-but-don't-quite cover.)

{gaps_md}

---

## Dimensions

These are cross-cutting attributes — money, deadlines, status, tech stack —
extracted directly from your notes (no LLM yet, just regex + heuristics).
They're useful on their own, before any edges get proposed.

{dimensions_md}

---

## Draft ontology

This is what the tool will use to propose edges. **Edit `.edge-finder/ontology.draft.yaml`
in your vault before running `edge-finder propose`** if anything looks off.

```yaml
{ontology_yaml}```

### Before continuing — does this fit your vault?

The shape above is a guess from what we observed. A few things to confirm:

1. Are **meeting → project** links the most useful, or would you rather
   prioritize **person → decision** or **topic → topic**?
2. **Action items** mentioned in meetings — link them as edges, or skip?
3. **Daily / journal notes** — treat as their own type, or fold into "meeting"?
4. Of the {n_islands} islands and {n_loops} centerless loops above — which
   ones bug you? (Often a sharper question than asking what relationships
   matter in the abstract.)

When the ontology and answers feel right, run `edge-finder propose` (coming next).
"""
