"""propose --plan: assemble candidate edges and write judgment-batch.md.

This is the ASSESS+PLAN phase. It does NOT call any LLM. It writes a
batch file the user reviews before running --judge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


from .cache import Cache, fingerprint, ontology_version, refresh_fingerprints
from .index import TfIdfIndex
from .topology import build_graph
from .walker import Note


@dataclass
class Candidate:
    src_path: str
    dst_path: str
    src_type: str
    dst_type: str
    score: float
    edge_type: str               # auto-assigned from ontology, or "related_to"
    edge_type_certain: bool      # True if the (src_type, dst_type) pair maps uniquely
    shared_terms: list[str] = field(default_factory=list)
    shared_term_count: int = 0


@dataclass
class PlanResult:
    candidates: list[Candidate] = field(default_factory=list)
    n_source_notes: int = 0
    n_skipped_already_linked: int = 0
    n_skipped_already_judged: int = 0
    n_skipped_short: int = 0
    n_skipped_thin_overlap: int = 0
    estimated_tokens: int = 0


def _edge_type_for(src_type: str, dst_type: str, ontology: dict) -> tuple[str, bool]:
    """Resolve the edge_type label for a (src_type, dst_type) pair."""
    edge_types = ontology.get("edge_types", [])
    matches = [
        e for e in edge_types
        if (e.get("from") == src_type or e.get("from") == "*")
        and (e.get("to") == dst_type or e.get("to") == "*")
        and e.get("name") != "related_to"
    ]
    if len(matches) == 1:
        return matches[0]["name"], True
    if len(matches) > 1:
        return matches[0]["name"], False
    return "related_to", False


def _ontology_score_boost(src_type: str, dst_type: str, ontology: dict, strict: bool) -> float | None:
    """Return a score multiplier, or None to drop the candidate.

    - In strict mode, type pairs without a declared (non-fallback) edge are dropped.
    - Otherwise, declared pairs get a 1.3x boost; unknown pairs stay at 1.0.
    """
    edge_types = ontology.get("edge_types", [])
    declared = [
        e for e in edge_types
        if e.get("name") != "related_to"
        and (e.get("from") == src_type or e.get("from") == "*")
        and (e.get("to") == dst_type or e.get("to") == "*")
    ]
    if declared:
        return 1.3
    if strict:
        return None
    return 1.0


def _priority(note: Note, by_path_type: dict[str, str], graph_degree: dict[str, int]) -> int:
    """Lower number = higher priority. Orphans first, then low-degree notes."""
    deg = graph_degree.get(note.relpath, 0)
    if deg == 0:
        return 0
    if deg == 1:
        return 1
    return 2 + deg


def plan(
    notes: list[Note],
    by_path_type: dict[str, str],
    ontology: dict,
    cache: Cache,
    *,
    k: int = 10,
    min_score: float = 0.10,
    min_words: int = 20,
    min_shared_terms: int = 3,
    budget: int | None = None,
    strict: bool = False,
) -> PlanResult:
    """Generate a planned batch of candidate edges. No LLM call."""
    # Refresh fingerprints from current notes
    refresh_fingerprints(cache, notes, by_path_type)
    cache.save_fingerprints()
    onto_v = ontology_version(ontology)

    # Build TF-IDF index
    index = TfIdfIndex.build(notes)

    # Build the existing-link graph so we can skip already-connected pairs
    g, _ = build_graph(notes)
    degree = dict(g.degree())

    # Existing edges set (undirected, sorted tuple)
    existing: set[tuple[str, str]] = set()
    for u, v in g.edges():
        existing.add(tuple(sorted((u, v))))

    # Sort sources by priority
    notes_by_path = {n.relpath: n for n in notes}
    sources = sorted(notes, key=lambda n: _priority(n, by_path_type, degree))

    result = PlanResult()
    seen_pairs: set[tuple[str, str]] = set()

    for src in sources:
        if src.word_count < min_words:
            result.n_skipped_short += 1
            continue
        if budget is not None and result.n_source_notes >= budget:
            break
        result.n_source_notes += 1

        src_sha = fingerprint(src)
        src_type = by_path_type.get(src.relpath, "generic")

        topk = index.top_k(src.relpath, k=k * 2, min_score=min_score)

        added = 0
        for dst_path, score in topk:
            if added >= k:
                break
            pair: tuple[str, str] = tuple(sorted((src.relpath, dst_path)))  # type: ignore[assignment]
            if pair in seen_pairs:
                continue
            if pair in existing:
                result.n_skipped_already_linked += 1
                continue

            dst_note = notes_by_path.get(dst_path)
            if dst_note is None:
                continue
            dst_sha = fingerprint(dst_note)
            if cache.has_verdict(src_sha, dst_sha, onto_v):
                result.n_skipped_already_judged += 1
                continue

            dst_type = by_path_type.get(dst_path, "generic")
            boost = _ontology_score_boost(src_type, dst_type, ontology, strict)
            if boost is None:
                continue

            # VALIDATION GATE — drop candidates with thin lexical overlap.
            # Without this, two short notes can score high purely on a single
            # rare word, which is a recipe for embarrassing edges.
            src_vec = index.docs.get(src.relpath, {})
            dst_vec = index.docs.get(dst_path, {})
            shared_keys = set(src_vec.keys()) & set(dst_vec.keys())
            if len(shared_keys) < min_shared_terms:
                result.n_skipped_thin_overlap += 1
                continue

            # Rank shared terms by min(src_weight, dst_weight) — the joint
            # importance — and keep the top few for visibility in the batch.
            shared_ranked = sorted(
                shared_keys,
                key=lambda t: -min(src_vec.get(t, 0.0), dst_vec.get(t, 0.0)),
            )
            top_shared = shared_ranked[:8]

            edge_type, certain = _edge_type_for(src_type, dst_type, ontology)
            result.candidates.append(Candidate(
                src_path=src.relpath,
                dst_path=dst_path,
                src_type=src_type,
                dst_type=dst_type,
                score=score * boost,
                edge_type=edge_type,
                edge_type_certain=certain,
                shared_terms=top_shared,
                shared_term_count=len(shared_keys),
            ))
            seen_pairs.add(pair)
            added += 1

    # Estimate tokens: rough — 250 tokens per pair (snippets + prompt)
    result.estimated_tokens = len(result.candidates) * 250
    return result


def write_batch(
    plan_result: PlanResult,
    notes: list[Note],
    ontology: dict,
    out_path: Path,
) -> None:
    """Write judgment-batch.md the user reviews before --judge."""
    notes_by_path = {n.relpath: n for n in notes}

    def snippet(n: Note, limit: int = 220) -> str:
        body = n.body.strip().replace("\n", " ")
        return (body[:limit] + "…") if len(body) > limit else body

    edge_lines = "\n".join(
        f"- `{e['name']}` ({e.get('from','*')} → {e.get('to','*')})"
        for e in ontology.get("edge_types", [])
    )

    parts = [
        "# Judgment Batch",
        "",
        "_Generated by `edge-finder propose --plan`. Read-only — no LLM has been called._",
        "",
        "## What's in here",
        "",
        f"- **{len(plan_result.candidates)} candidate edges** to judge",
        f"- **{plan_result.n_source_notes} source notes** considered (priority: orphans first)",
        f"- **{plan_result.n_skipped_already_linked}** pairs skipped (already linked)",
        f"- **{plan_result.n_skipped_already_judged}** pairs skipped (verdict cached)",
        f"- **{plan_result.n_skipped_short}** notes skipped (too short to judge)",
        f"- **{plan_result.n_skipped_thin_overlap}** pairs dropped by validation gate (<3 shared substantive terms)",
        f"- estimated cost: **~{plan_result.estimated_tokens:,} tokens** if all are judged",
        "",
        "## Allowed edge types (from your ontology)",
        "",
        edge_lines or "- _(no edge_types declared)_",
        "",
        "---",
        "",
        "## Candidates",
        "",
        "Each candidate has a deterministically-assigned edge_type when the",
        "(source → target) type pair maps uniquely. Otherwise the edge_type",
        "is suggested and the LLM may revise it during `--judge`.",
        "",
        "Delete any candidate block below before running `--judge` to skip it.",
        "",
    ]

    for i, c in enumerate(plan_result.candidates, 1):
        src = notes_by_path.get(c.src_path)
        dst = notes_by_path.get(c.dst_path)
        if not src or not dst:
            continue
        certainty = "deterministic" if c.edge_type_certain else "suggested"
        parts.append(f"### {i}. `{c.src_path}` → `{c.dst_path}`")
        parts.append("")
        parts.append(f"- **score**: {c.score:.3f}")
        parts.append(f"- **types**: {c.src_type} → {c.dst_type}")
        parts.append(f"- **edge_type**: `{c.edge_type}` ({certainty})")
        shared_str = ", ".join(f"`{t}`" for t in c.shared_terms)
        parts.append(f"- **shared terms** ({c.shared_term_count}): {shared_str}")
        parts.append("")
        parts.append(f"**Source snippet** ({src.word_count} words):")
        parts.append(f"> {snippet(src)}")
        parts.append("")
        parts.append(f"**Target snippet** ({dst.word_count} words):")
        parts.append(f"> {snippet(dst)}")
        parts.append("")
        parts.append("---")
        parts.append("")

    out_path.write_text("\n".join(parts), encoding="utf-8")
