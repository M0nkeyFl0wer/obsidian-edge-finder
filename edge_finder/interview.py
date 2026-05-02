"""Ontology interview: scan vault → derive signals → produce questions.

The interview is how a vault gets its ontology. Edge-finder reads
the user's actual content, surfaces shape signals, and generates
targeted questions. The user answers in `interview-prep.md` (markdown
checkboxes, same UX as proposals.md / triage-plan.md). Then
`edge-finder ontology --apply` reads the answers and composes the
vault's `.edge-finder/ontology.yaml`.

Profiles in `ontology/profiles/` are the *source* of candidate offers
— never auto-applied. When the interview detects Granola-shape, it
pulls candidate predicates from `granola-meetings.yaml` and offers
each one individually.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from . import ontology as onto
from .stubs import find_stubs
from .walker import Note


@dataclass
class InterviewSignals:
    """Structural signals derived from the vault."""
    n_notes: int
    granola_share: float       # fraction of notes with Granola sections
    stub_share: float          # fraction of notes that match the stub heuristic
    type_counts: Counter       # frontmatter `type:` value → count
    top_tags: list[tuple[str, int]]   # (tag, count) sorted desc
    folder_counts: Counter     # top-level folder → note count
    author_wikilinks: int      # distinct wikilinks appearing in `author:` fm
    domain_counts: Counter     # publisher domains seen in URL frontmatter
    has_clippings: bool


@dataclass
class Question:
    """A single yes/no offer the interview puts to the user.

    `kind` groups questions by section in interview-prep.md. `answer`
    represents the structural change applied if the user says yes —
    e.g., adding a node type or a predicate to the vault layer.
    """
    id: str
    kind: str                  # "node_type" | "predicate" | "tag_promotion" | "folder_role"
    headline: str
    detail: str
    payload: dict              # what to add to vault layer if accepted
    default_checked: bool = False


# ---------- Signal collection -------------------------------------------------


_GRANOLA_SECTIONS = ("## Summary", "## Attendees", "## Action Items", "## Decisions")
_BORING_TAGS = {
    "bookmark", "bookmarks", "clipping", "clippings", "stub", "web-content",
    "web-clip", "pocket", "firefox", "flipboard", "raindrop", "instapaper",
    "note", "import", "imported", "untagged", "todo", "draft",
    "hub", "auto-generated", "overview",
}
_URL_RE = re.compile(r"https?://([^/\s]+)")


def _has_granola_shape(body: str) -> bool:
    return sum(1 for s in _GRANOLA_SECTIONS if s in body) >= 2


def _publisher_domain(fm: dict) -> str | None:
    for k in ("source", "url", "link"):
        v = fm.get(k)
        if isinstance(v, str):
            m = _URL_RE.search(v)
            if m:
                return m.group(1).lower().lstrip("www.")
    return None


def gather_signals(notes: list[Note]) -> InterviewSignals:
    n = len(notes)
    granola = sum(1 for x in notes if _has_granola_shape(x.body))
    stubs = find_stubs(notes)
    type_counts: Counter = Counter()
    top_tags: Counter = Counter()
    folder_counts: Counter = Counter()
    author_wikilinks: set[str] = set()
    domain_counts: Counter = Counter()

    wikilink_re = re.compile(r"\[\[([^\]\|#]+)")
    for note in notes:
        ft = note.frontmatter.get("type")
        if isinstance(ft, str) and ft:
            type_counts[ft] += 1
        for t in note.tags:
            tl = t.lower()
            if tl not in _BORING_TAGS:
                top_tags[tl] += 1
        parts = note.relpath.replace("\\", "/").split("/")
        folder_counts[parts[0] if len(parts) > 1 else "<root>"] += 1
        author = note.frontmatter.get("author")
        if isinstance(author, str):
            for m in wikilink_re.finditer(author):
                author_wikilinks.add(m.group(1).strip())
        elif isinstance(author, list):
            for a in author:
                if isinstance(a, str):
                    for m in wikilink_re.finditer(a):
                        author_wikilinks.add(m.group(1).strip())
        d = _publisher_domain(note.frontmatter)
        if d:
            domain_counts[d] += 1

    has_clippings = folder_counts.get("Clippings", 0) > 0

    return InterviewSignals(
        n_notes=n,
        granola_share=granola / n if n else 0.0,
        stub_share=len(stubs) / n if n else 0.0,
        type_counts=type_counts,
        top_tags=top_tags.most_common(20),
        folder_counts=folder_counts,
        author_wikilinks=len(author_wikilinks),
        domain_counts=domain_counts,
        has_clippings=has_clippings,
    )


# ---------- Question generation -----------------------------------------------


def _profile_predicates(profile_name: str) -> list[dict]:
    try:
        return onto.load_profile(profile_name).get("predicates") or []
    except FileNotFoundError:
        return []


def _profile_node_types(profile_name: str) -> list[dict]:
    try:
        return onto.load_profile(profile_name).get("node_types") or []
    except FileNotFoundError:
        return []


def build_questions(signals: InterviewSignals) -> list[Question]:
    questions: list[Question] = []

    # Granola shape — offer meeting-related types and predicates
    if signals.granola_share >= 0.10:
        share = round(100 * signals.granola_share)
        for nt in _profile_node_types("granola-meetings"):
            questions.append(Question(
                id=f"granola-type-{nt['name']}",
                kind="node_type",
                headline=f"Add `{nt['name']}` as a node type? (Granola profile)",
                detail=(
                    f"{share}% of your notes have Granola-style structured "
                    f"sections. {nt['description'].strip()}"
                ),
                payload={"node_type": nt},
                default_checked=signals.granola_share >= 0.30,
            ))
        for p in _profile_predicates("granola-meetings"):
            questions.append(Question(
                id=f"granola-pred-{p['name']}",
                kind="predicate",
                headline=f"Add `{p['name']}` predicate? (Granola profile)",
                detail=p.get("description", "").strip(),
                payload={"predicate": p},
                default_checked=signals.granola_share >= 0.30,
            ))

    # Bookmark/stub shape — offer bookmark-related types and predicates
    if signals.stub_share >= 0.10:
        share = round(100 * signals.stub_share)
        for nt in _profile_node_types("bookmark-import"):
            questions.append(Question(
                id=f"bookmark-type-{nt['name']}",
                kind="node_type",
                headline=f"Add `{nt['name']}` as a node type? (Bookmark profile)",
                detail=(
                    f"{share}% of your notes look like URL-stub bookmark imports. "
                    f"{nt['description'].strip()}"
                ),
                payload={"node_type": nt},
                default_checked=signals.stub_share >= 0.30,
            ))
        for p in _profile_predicates("bookmark-import"):
            questions.append(Question(
                id=f"bookmark-pred-{p['name']}",
                kind="predicate",
                headline=f"Add `{p['name']}` predicate? (Bookmark profile)",
                detail=p.get("description", "").strip(),
                payload={"predicate": p},
                default_checked=signals.stub_share >= 0.30,
            ))

    # Tag promotion — high-frequency tags become hub or concept candidates
    for tag, count in signals.top_tags:
        if count < 10:
            continue
        questions.append(Question(
            id=f"tag-hub-{tag}",
            kind="tag_promotion",
            headline=f"Promote `#{tag}` ({count} notes) to a hub?",
            detail=(
                f"A hub is queryable as `topic_hub:: [[{tag.title()} Hub]]`. "
                f"Use this when you want notes tagged `#{tag}` to be browsable as "
                f"a topical aggregator."
            ),
            payload={"tag_promotion": {"tag": tag, "as": "hub", "count": count}},
            default_checked=count >= 50,
        ))
        questions.append(Question(
            id=f"tag-concept-{tag}",
            kind="tag_promotion",
            headline=f"Promote `#{tag}` ({count} notes) to a concept entity?",
            detail=(
                f"A concept is queryable as `discusses:: [[{tag}]]`. "
                f"Use this when you want notes that *cover* the topic to point at "
                f"it from their body, distinct from notes that are *about* it."
            ),
            payload={"tag_promotion": {"tag": tag, "as": "concept", "count": count}},
            default_checked=False,
        ))

    # Author wikilinks — promote to person type if substantial
    if signals.author_wikilinks >= 5:
        questions.append(Question(
            id="promote-author-person",
            kind="node_type",
            headline=f"Treat `author:` wikilinks as `person` entities? "
                     f"({signals.author_wikilinks} distinct authors detected)",
            detail=(
                "Adds the `authored_by` predicate (already in core) and treats "
                "any wikilink target referenced via the `author:` frontmatter "
                "field as a `person` node."
            ),
            payload={"author_promotion": True},
            default_checked=signals.author_wikilinks >= 10,
        ))

    # Domain promotion — frequent publishers become org candidates
    for domain, count in signals.domain_counts.most_common(10):
        if count < 5:
            continue
        questions.append(Question(
            id=f"domain-org-{domain}",
            kind="folder_role",
            headline=f"Promote `{domain}` ({count} notes) to an `org` entity?",
            detail=(
                f"Adds a node `[[{domain}]]` typed as `org`, and sets "
                f"`published_in:: [[{domain}]]` on each note from this domain. "
                f"Enables 2-hop queries like 'all climate notes published in this outlet'."
            ),
            payload={"org_promotion": {"domain": domain, "count": count}},
            default_checked=count >= 20,
        ))

    return questions


# ---------- Markdown rendering ------------------------------------------------


def render_interview_md(signals: InterviewSignals, questions: list[Question]) -> str:
    parts = [
        "# Ontology Interview",
        "",
        "_Generated by `edge-finder ontology`. Each section below offers a_",
        "_yes/no decision about your vault's schema. Check the boxes you want_",
        "_applied; uncheck the rest. Then run:_",
        "",
        "    edge-finder ontology --apply <vault>",
        "",
        "_The composed schema is written to `.edge-finder/ontology.yaml` and_",
        "_becomes the contract for `triage`, `propose`, and `apply` going forward._",
        "",
        "## Vault snapshot",
        "",
        f"- **{signals.n_notes}** notes total",
        f"- **{round(100*signals.granola_share)}%** Granola-shaped (≥2 of `## Summary` / `## Attendees` / `## Action Items` / `## Decisions`)",
        f"- **{round(100*signals.stub_share)}%** URL-stub bookmark imports",
        f"- **{len(signals.type_counts)}** distinct frontmatter `type:` values",
        f"- **{signals.author_wikilinks}** distinct author wikilinks",
        f"- **{len(signals.domain_counts)}** distinct publisher domains",
        f"- **Clippings/ folder present:** {'yes' if signals.has_clippings else 'no'}",
        "",
    ]
    if signals.type_counts:
        parts.append("**Top frontmatter types:**")
        for t, c in signals.type_counts.most_common(8):
            parts.append(f"- `{t}` × {c}")
        parts.append("")
    if signals.top_tags:
        parts.append("**Top non-boring tags:**")
        for t, c in signals.top_tags[:10]:
            parts.append(f"- `#{t}` × {c}")
        parts.append("")
    parts.extend(["---", ""])

    # Group questions by kind for readable section headers
    sections: dict[str, list[Question]] = {}
    for q in questions:
        sections.setdefault(q.kind, []).append(q)

    section_titles = {
        "node_type": "Node types",
        "predicate": "Predicates",
        "tag_promotion": "Tag promotions",
        "folder_role": "Folder & domain roles",
    }
    for kind, qs in sections.items():
        parts.append(f"## {section_titles.get(kind, kind.title())}")
        parts.append("")
        for q in qs:
            box = "x" if q.default_checked else " "
            parts.append(f"### `{q.id}`")
            parts.append(f"- [{box}] {q.headline}")
            parts.append(f"- {q.detail}")
            parts.append("")
    return "\n".join(parts) + "\n"


# ---------- Apply -------------------------------------------------------------


_QUESTION_HEADER_RE = re.compile(r"^### `([^`]+)`\s*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^- \[(.)\]\s+", re.MULTILINE)


def parse_answers(md_text: str, questions: list[Question]) -> list[Question]:
    """Parse interview-prep.md back, return the questions the user checked."""
    by_id = {q.id: q for q in questions}
    accepted: list[Question] = []
    headers = list(_QUESTION_HEADER_RE.finditer(md_text))
    for i, m in enumerate(headers):
        qid = m.group(1)
        if qid not in by_id:
            continue
        block_start = m.end()
        block_end = headers[i + 1].start() if i + 1 < len(headers) else len(md_text)
        block = md_text[block_start:block_end]
        cb = _CHECKBOX_RE.search(block)
        if cb and cb.group(1).strip().lower() == "x":
            accepted.append(by_id[qid])
    return accepted


def compose_vault_layer(accepted: list[Question]) -> dict:
    """Build the vault-specific ontology layer YAML from accepted questions."""
    node_types: list[dict] = []
    predicates: list[dict] = []
    tag_promotions: list[dict] = []
    domain_promotions: list[dict] = []
    author_promotion = False

    for q in accepted:
        if "node_type" in q.payload:
            node_types.append(q.payload["node_type"])
        elif "predicate" in q.payload:
            predicates.append(q.payload["predicate"])
        elif "tag_promotion" in q.payload:
            tag_promotions.append(q.payload["tag_promotion"])
        elif "org_promotion" in q.payload:
            domain_promotions.append(q.payload["org_promotion"])
        elif q.payload.get("author_promotion"):
            author_promotion = True

    layer: dict = {
        "version": 1.0,
        "layer": "vault",
        "extends": "core",
    }
    if node_types:
        layer["node_types"] = node_types
    if predicates:
        layer["predicates"] = predicates
    if tag_promotions or domain_promotions or author_promotion:
        layer["promotions"] = {}
        if tag_promotions:
            layer["promotions"]["tags"] = tag_promotions
        if domain_promotions:
            layer["promotions"]["domains"] = domain_promotions
        if author_promotion:
            layer["promotions"]["authors_as_persons"] = True
    return layer
