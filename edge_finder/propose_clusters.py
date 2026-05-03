"""Enhanced triplet closure - finds all cluster-based triplet connections."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ontology import Ontology
from .topology import build_graph
from .triage import find_hubs
from .typed_edges import infer_note_type
from .walker import Note


@dataclass
class ClusterProposal:
    source: str
    target: str
    predicate: str
    confidence: str
    rationale: str
    cluster_type: str  # author, publisher, language, tag, folder, concept


def plan_cluster_proposals(
    notes: list[Note],
    ontology: Ontology,
    *,
    max_per_type: int = 50,
) -> list[ClusterProposal]:
    """Find all cluster-based triplet closures."""
    graph, _ = build_graph(notes)
    note_by_path = {n.relpath: n for n in notes}
    existing = {tuple(sorted((u, v))) for u, v in graph.edges()}

    proposals: list[ClusterProposal] = []

    # 1. Author clusters (books)
    proposals.extend(_find_author_clusters(notes, note_by_path, existing, max_per_type))

    # 2. Publisher clusters (books)
    proposals.extend(_find_publisher_clusters(notes, note_by_path, existing, max_per_type))

    # 3. Language clusters (repos)
    proposals.extend(_find_language_clusters(notes, note_by_path, existing, max_per_type))

    # 4. Tag clusters (excluding boring tags)
    proposals.extend(_find_tag_clusters(notes, note_by_path, existing, max_per_type))

    # 5. Folder clusters
    proposals.extend(_find_folder_clusters(notes, note_by_path, existing, max_per_type))

    return proposals


def _find_author_clusters(notes, note_by_path, existing, max_per_type):
    """Find books by same author."""
    from collections import defaultdict
    author_groups = defaultdict(list)

    for n in notes:
        if 'books/' not in str(n.relpath):
            continue
        author = n.frontmatter.get('author', '')
        if author and isinstance(author, str) and author.strip():
            author_groups[author.strip()].append(n.relpath)

    proposals = []
    for author, paths in author_groups.items():
        if len(paths) < 2:
            continue
        for i, src in enumerate(paths):
            for tgt in paths[i+1:]:
                pair = tuple(sorted((src, tgt)))
                if pair in existing:
                    continue
                proposals.append(ClusterProposal(
                    source=src,
                    target=tgt,
                    predicate="co_topic",
                    confidence="high",
                    rationale=f"same author: {author}",
                    cluster_type="author",
                ))
                if len(proposals) >= max_per_type:
                    break
    return proposals


def _find_publisher_clusters(notes, note_by_path, existing, max_per_type):
    """Find books by same publisher."""
    from collections import defaultdict
    pub_groups = defaultdict(list)

    for n in notes:
        if 'books/' not in str(n.relpath):
            continue
        publisher = n.frontmatter.get('publisher', '')
        if publisher and isinstance(publisher, str) and publisher.strip():
            pub_groups[publisher.strip()].append(n.relpath)

    proposals = []
    for publisher, paths in pub_groups.items():
        if len(paths) < 2:
            continue
        for i, src in enumerate(paths):
            for tgt in paths[i+1:]:
                pair = tuple(sorted((src, tgt)))
                if pair in existing:
                    continue
                proposals.append(ClusterProposal(
                    source=src,
                    target=tgt,
                    predicate="co_topic",
                    confidence="high",
                    rationale=f"same publisher: {publisher}",
                    cluster_type="publisher",
                ))
                if len(proposals) >= max_per_type:
                    break
    return proposals


def _find_language_clusters(notes, note_by_path, existing, max_per_type):
    """Find repos using same language."""
    from collections import defaultdict
    lang_groups = defaultdict(list)

    for n in notes:
        if 'repositories/' not in str(n.relpath):
            continue
        lang = n.frontmatter.get('language', '')
        if lang and isinstance(lang, str) and lang.strip():
            lang_groups[lang.strip()].append(n.relpath)

    proposals = []
    for lang, paths in lang_groups.items():
        if len(paths) < 2:
            continue
        for i, src in enumerate(paths):
            for tgt in paths[i+1:]:
                pair = tuple(sorted((src, tgt)))
                if pair in existing:
                    continue
                proposals.append(ClusterProposal(
                    source=src,
                    target=tgt,
                    predicate="co_topic",
                    confidence="high",
                    rationale=f"same language: {lang}",
                    cluster_type="language",
                ))
                if len(proposals) >= max_per_type:
                    break
    return proposals


def _find_tag_clusters(notes, note_by_path, existing, max_per_type):
    """Find notes sharing specific tags."""
    from collections import defaultdict
    BORING = {'bookmark', 'bookmarks', 'pocket', 'firefox', 'web-content', 'stub'}

    tag_groups = defaultdict(list)
    for n in notes:
        for tag in getattr(n, 'tags', []):
            if tag.lower() not in BORING:
                tag_groups[tag].append(n.relpath)

    proposals = []
    for tag, paths in tag_groups.items():
        if len(paths) < 3:  # Need at least 3 for triplets
            continue
        for i, src in enumerate(paths):
            for tgt in paths[i+1:]:
                pair = tuple(sorted((src, tgt)))
                if pair in existing:
                    continue
                proposals.append(ClusterProposal(
                    source=src,
                    target=tgt,
                    predicate="co_topic",
                    confidence="medium",
                    rationale=f"shared tag: {tag}",
                    cluster_type="tag",
                ))
                if len(proposals) >= max_per_type:
                    break
    return proposals


def _find_folder_clusters(notes, note_by_path, existing, max_per_type):
    """Find notes in same folder."""
    from collections import defaultdict
    folder_groups = defaultdict(list)

    for n in notes:
        parts = str(n.relpath).split('/')
        if len(parts) >= 2:
            key = '/'.join(parts[:2])
            folder_groups[key].append(n.relpath)

    proposals = []
    for folder, paths in folder_groups.items():
        if len(paths) < 2:
            continue
        for i, src in enumerate(paths):
            for tgt in paths[i+1:]:
                pair = tuple(sorted((src, tgt)))
                if pair in existing:
                    continue
                proposals.append(ClusterProposal(
                    source=src,
                    target=tgt,
                    predicate="co_topic",
                    confidence="medium",
                    rationale=f"same folder: {folder}",
                    cluster_type="folder",
                ))
                if len(proposals) >= max_per_type:
                    break
    return proposals


def write_cluster_proposals_md(proposals: list[ClusterProposal], out_path: Path) -> None:
    """Write proposals to markdown for review."""
    parts = [
        "# Proposals",
        "",
        "Generated by `edge-finder propose --mode clusters`.",
        "Cluster-based triplet closures (author, publisher, language, tag, folder).",
        "",
        "Check the box next to proposals you want applied. Then run:",
        "    edge-finder apply <vault>",
        "",
        "---",
        "",
    ]
    for i, proposal in enumerate(proposals, 1):
        parts.append(f"## {i}. `{proposal.source}` → `{proposal.target}`")
        parts.append("")
        parts.append(f"- [x] Apply: predicate=`{proposal.predicate}`, confidence={proposal.confidence}")
        parts.append(f"- Cluster: {proposal.cluster_type}")
        parts.append(f"- Rationale: {proposal.rationale}")
        parts.append("")
    if not proposals:
        parts.append("_No cluster-based closures were found._")
        parts.append("")
    out_path.write_text("\n".join(parts), encoding="utf-8")
    return proposals
