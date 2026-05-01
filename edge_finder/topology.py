"""Find structural gaps in the vault's link graph.

Three findings, each maps to a classical graph-topology idea:
  - islands         → connected components (H0)
  - load-bearing    → articulation points (cut vertices)
  - centerless loop → chordless cycle of length >= 4 (H1 candidates)

We work on the undirected graph built from existing [[wikilinks]]. Persistent
homology over an embedding distance matrix is a richer view but it's not
needed for the introductory report — NetworkX gets us the headline gaps.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import networkx as nx

from .walker import Note


@dataclass
class GapsReport:
    n_nodes: int
    n_edges: int
    n_islands: int = 0
    n_orphans: int = 0
    n_articulations: int = 0
    n_loops: int = 0
    n_cocitations: int = 0
    n_subclusters: int = 0
    islands: list[list[str]] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)
    articulation_points: list[tuple[str, int]] = field(default_factory=list)
    centerless_loops: list[list[str]] = field(default_factory=list)
    cocitations: list[tuple[str, str, int, list[str]]] = field(default_factory=list)
    # subclusters: list of (cluster_label, member_paths) within the biggest island
    subclusters: list[tuple[str, list[str]]] = field(default_factory=list)


def _resolve_link(target: str, title_to_path: dict[str, str]) -> str | None:
    """Return the canonical relpath for a [[link]] target, or None if dangling."""
    target = target.strip()
    if not target:
        return None
    if target in title_to_path:
        return title_to_path[target]
    lower = target.lower()
    for k, v in title_to_path.items():
        if k.lower() == lower:
            return v
    return None


def build_graph(notes: list[Note]) -> tuple[nx.Graph, dict[str, str]]:
    """Build an undirected graph keyed by note relpath.

    Edges come from existing [[wikilinks]] only. Aliases are honored when
    resolving link targets to a canonical path.
    """
    title_to_path: dict[str, str] = {}
    for n in notes:
        title_to_path.setdefault(n.title, n.relpath)
        title_to_path.setdefault(n.path.stem, n.relpath)
        for alias in n.aliases:
            title_to_path.setdefault(alias, n.relpath)

    g: nx.Graph = nx.Graph()
    for n in notes:
        g.add_node(n.relpath)
    for n in notes:
        for raw in n.wikilinks:
            target = _resolve_link(raw, title_to_path)
            if target and target != n.relpath:
                g.add_edge(n.relpath, target)
    return g, title_to_path


def _chordless_cycles(g: nx.Graph, max_len: int = 8, max_results: int = 5) -> list[list[str]]:
    """Find a few chordless cycles of length 3..max_len, longest first.

    A chordless cycle is a loop with no shortcut edges between non-adjacent
    members — the topological signature of a "conversation circling a topic
    with no synthesis note in the middle". We rank by length descending
    because longer loops are stronger gap signals (bigger circle, more
    obvious missing center) and triangles tend to be noisier.
    """
    found: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    try:
        cycles = nx.cycle_basis(g)
    except nx.NetworkXError:
        return []
    cycles.sort(key=lambda c: -len(c))
    for cyc in cycles:
        if not (3 <= len(cyc) <= max_len):
            continue
        key = frozenset(cyc)
        if key in seen:
            continue
        chord = False
        members = set(cyc)
        for i, u in enumerate(cyc):
            for j, v in enumerate(cyc):
                if j <= i:
                    continue
                if (j - i) in (1, len(cyc) - 1):
                    continue
                if g.has_edge(u, v):
                    chord = True
                    break
            if chord:
                break
        if chord:
            continue
        seen.add(key)
        found.append(cyc)
        if len(found) >= max_results:
            break
    return found


def _cocitations(g: nx.Graph, min_witnesses: int = 2, max_results: int = 10) -> list[tuple[str, str, int, list[str]]]:
    """Find pairs frequently cited together but not directly linked.

    For each node `c`, every pair of its neighbors (a, b) is "co-cited by c".
    A high-value gap is a pair (a, b) co-cited by >= min_witnesses different
    nodes, where (a, b) is not itself an edge in the graph.

    Returns: [(a, b, witness_count, sample_witnesses)], strongest first.
    """
    from collections import defaultdict
    from itertools import combinations

    counts: dict[tuple[str, str], list[str]] = defaultdict(list)
    for c in g.nodes():
        neighbors = list(g.neighbors(c))
        if len(neighbors) < 2:
            continue
        for a, b in combinations(neighbors, 2):
            pair = tuple(sorted((a, b)))
            counts[pair].append(c)

    out: list[tuple[str, str, int, list[str]]] = []
    for (a, b), witnesses in counts.items():
        if len(witnesses) < min_witnesses:
            continue
        if g.has_edge(a, b):
            continue
        out.append((a, b, len(witnesses), witnesses[:3]))
    out.sort(key=lambda r: -r[2])
    return out[:max_results]


def _subclusters(g: nx.Graph, biggest: list[str], min_island_size: int = 30) -> list[tuple[str, list[str]]]:
    """Run greedy modularity community detection on the biggest island.

    Only worth running when the island is big enough that "it has sub-clusters"
    is more useful than just listing its members. Returns sub-clusters of >= 5 nodes.
    """
    if len(biggest) < min_island_size:
        return []
    sub = g.subgraph(biggest).copy()
    try:
        communities = nx.community.greedy_modularity_communities(sub)
    except Exception:
        return []

    out: list[tuple[str, list[str]]] = []
    for i, comm in enumerate(sorted((list(c) for c in communities), key=len, reverse=True)):
        if len(comm) < 5:
            continue
        # Label the cluster by the highest-degree member's stem-ish path
        comm_sorted_by_deg = sorted(comm, key=lambda n: -sub.degree(n))
        label_seed = comm_sorted_by_deg[0]
        label = label_seed.rsplit("/", 1)[-1].rsplit(".md", 1)[0]
        out.append((f"sub-cluster {i+1}: ~{label}", comm))
        if len(out) >= 5:
            break
    return out


def find_gaps(notes: list[Note]) -> tuple[GapsReport, nx.Graph]:
    g, _ = build_graph(notes)
    components = sorted((list(c) for c in nx.connected_components(g)), key=len, reverse=True)
    islands = [c for c in components if len(c) >= 2]
    orphans = [n for c in components for n in c if len(c) == 1]
    articulations = list(nx.articulation_points(g))
    art_with_degree = sorted(((n, g.degree(n)) for n in articulations), key=lambda x: -x[1])[:10]
    loops = _chordless_cycles(g)
    cocitations = _cocitations(g)
    subclusters = _subclusters(g, islands[0]) if islands else []

    report = GapsReport(
        n_nodes=g.number_of_nodes(),
        n_edges=g.number_of_edges(),
        n_islands=len(islands),
        n_orphans=len(orphans),
        n_articulations=len(articulations),
        n_loops=len(loops),
        n_cocitations=len(cocitations),
        n_subclusters=len(subclusters),
        islands=islands[:10],
        orphans=orphans[:50],
        articulation_points=art_with_degree,
        centerless_loops=loops,
        cocitations=cocitations,
        subclusters=subclusters,
    )
    return report, g


def islands_summary(islands: list[list[str]]) -> str:
    """Render a one-line summary of island sizes."""
    if not islands:
        return "no multi-note islands"
    sizes = Counter(len(c) for c in islands)
    parts = [f"{count}× size-{size}" for size, count in sorted(sizes.items(), reverse=True)]
    return ", ".join(parts)
