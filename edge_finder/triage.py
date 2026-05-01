"""edge-finder triage — propose and apply hub-attach edges for stub notes.

Triage runs in two phases like the rest of the workflow:

  1. Plan (offline): scan the vault, detect stubs (via stubs.py),
     identify hub-shaped notes, match stub tags against hub names,
     emit `triage-plan.md` with markdown checkboxes.

  2. Apply (offline mutation): read the user-checked plan, add a
     single `## See also\\n- [[hub]]\\n` block to each checked stub.
     Hub notes are NOT modified — adding 1k inbound bullets to a
     hub would clutter it. Forward edges are enough for traversal.

This is the deterministic, no-LLM workflow for vaults with bookmark
imports. After triage, `propose --judge` will have meaningful work
to do because the rest of the vault has fewer dark-matter orphans.
"""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import networkx as nx

from .stubs import StubInfo, find_stubs
from .topology import build_graph
from .walker import Note, walk_vault

_HUB_NAME_RE = re.compile(r"\b(kanban|index|overview|hub|map|catalog|directory)\b", re.IGNORECASE)
_NORM_RE = re.compile(r"[^a-z0-9]+")
_HEADING_RE = re.compile(r"^##\s+\d+\.\s+`([^`]+)`\s*→\s*`([^`]+)`\s*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s+\[(.)\]\s+Attach\b", re.MULTILINE)
_SEE_ALSO_RE = re.compile(r"^##\s+See also\s*$", re.MULTILINE)


def _norm(s: str) -> str:
    """Lowercase + strip non-alphanumeric for tag/hub matching."""
    return _NORM_RE.sub("", s.lower())


@dataclass
class Hub:
    relpath: str
    title: str
    slug: str           # filename without .md, kept verbatim for display
    norm_slug: str      # normalized for matching
    norm_title: str
    degree: int


@dataclass
class HubAttach:
    """A proposed `stub → hub` attachment."""
    stub_relpath: str
    hub_relpath: str
    matched_tag: str
    confidence: str = "high"


@dataclass
class TriagePlan:
    n_stubs: int
    n_hubs: int
    attachments: list[HubAttach] = field(default_factory=list)
    unattached: list[StubInfo] = field(default_factory=list)


@dataclass
class ApplyResult:
    proposals_total: int = 0
    proposals_checked: int = 0
    edges_added: int = 0
    files_touched: int = 0
    backup_path: Path | None = None
    errors: list[str] = field(default_factory=list)


# ---------- Hub identification --------------------------------------------------


def _is_hub_name(name: str) -> bool:
    """Filename slug looks like a hub (Kanban / Index / Overview / Hub / Map)."""
    return bool(_HUB_NAME_RE.search(name))


_CONTENT_FOLDERS = {"books", "web-clips", "Clippings", "07_Bookmarks", "daily", "Daily"}


def find_hubs(notes: list[Note], graph: nx.Graph) -> list[Hub]:
    """Return notes that look like hubs.

    A hub is a note whose filename matches a hub-shaped pattern
    (Kanban / Index / Overview / Hub / Map / Catalog / Directory) AND
    is not inside a content folder (books/, web-clips/, Clippings/,
    daily/, etc).

    We deliberately do NOT use degree as a fallback signal: book notes
    and other content nodes get high degree from cross-references but
    are not topical hubs. False-attaching a `#crypto` tag to
    `books/crypto.md` (a book about crypto) instead of a `Crypto
    Kanban.md` (a topical hub) is the failure mode this avoids.
    """
    hubs: list[Hub] = []
    seen: set[str] = set()
    for n in notes:
        slug = n.path.stem
        if not _is_hub_name(slug):
            continue
        first_part = n.relpath.split("/", 1)[0] if "/" in n.relpath else ""
        if first_part in _CONTENT_FOLDERS:
            continue
        if n.relpath in seen:
            continue
        seen.add(n.relpath)
        deg = graph.degree(n.relpath) if n.relpath in graph else 0
        hubs.append(Hub(
            relpath=n.relpath,
            title=n.title,
            slug=slug,
            norm_slug=_norm(slug),
            norm_title=_norm(n.title),
            degree=deg,
        ))
    return hubs


# ---------- Tag → hub matching --------------------------------------------------


def match_tag_to_hubs(tag: str, hubs: list[Hub]) -> list[Hub]:
    """Return all hubs whose normalized name contains the normalized tag.

    Caller is responsible for tiebreaking (we sort by degree-desc here so
    the canonical hub for a topic comes first when multiple match).
    """
    nt = _norm(tag)
    if len(nt) < 3:
        # Avoid matching 1-2 letter tags against 'index'/'hub' substrings.
        return []
    matches = [
        h for h in hubs
        if nt == h.norm_slug or nt in h.norm_slug or nt in h.norm_title
    ]
    matches.sort(key=lambda h: -h.degree)
    return matches


# ---------- Plan ----------------------------------------------------------------


def plan_triage(notes: list[Note], graph: nx.Graph) -> TriagePlan:
    """Walk the vault, detect stubs, match each stub's tags to hubs.

    A stub yields zero or one HubAttach (the highest-degree hub whose
    name matches one of the stub's topical tags). Stubs with no usable
    tag, or whose tags only match boilerplate ("bookmark", "clipping"),
    end up in `unattached` for the report.
    """
    stubs = find_stubs(notes)
    hubs = find_hubs(notes, graph)
    plan = TriagePlan(n_stubs=len(stubs), n_hubs=len(hubs))

    for s in stubs:
        chosen: HubAttach | None = None
        for tag in s.candidate_hub_tags:
            matches = match_tag_to_hubs(tag, hubs)
            if not matches:
                continue
            chosen = HubAttach(
                stub_relpath=s.relpath,
                hub_relpath=matches[0].relpath,
                matched_tag=tag,
                confidence="high" if matches[0].degree >= 3 else "medium",
            )
            break
        if chosen:
            plan.attachments.append(chosen)
        else:
            plan.unattached.append(s)
    return plan


def render_plan_md(plan: TriagePlan) -> str:
    """Render triage-plan.md — the file the user reviews and checks."""
    parts = [
        "# Triage Plan",
        "",
        "_Generated by `edge-finder triage`. Each line below is a proposed_",
        "_`stub → hub` wikilink. **Checked** boxes will be applied; uncheck_",
        "_to skip. Then run: `edge-finder triage --apply <vault>`._",
        "",
        f"- **{plan.n_stubs}** stub notes detected",
        f"- **{plan.n_hubs}** hub-shaped notes available",
        f"- **{len(plan.attachments)}** stubs matched to a hub via tag (proposed below)",
        f"- **{len(plan.unattached)}** stubs left unattached (no usable tag-hub match)",
        "",
        "---",
        "",
        "## Attachments",
        "",
    ]
    if not plan.attachments:
        parts.append("_No stub→hub attachments could be proposed automatically._")
    for i, a in enumerate(plan.attachments, 1):
        parts.append(f"## {i}. `{a.stub_relpath}` → `{a.hub_relpath}`")
        parts.append("")
        parts.append(f"- [x] Attach via tag `{a.matched_tag}` (confidence={a.confidence})")
        parts.append("")
    parts.append("\n---\n")
    if plan.unattached:
        parts.append(f"\n## Unattached stubs ({len(plan.unattached)})")
        parts.append("")
        parts.append("_These stubs had no tags matching a hub. Most are URL-only_")
        parts.append("_imports without topical metadata. Consider re-clipping the_")
        parts.append("_high-value ones via Obsidian Web Clipper to enrich them._")
        parts.append("")
        for s in plan.unattached[:25]:
            parts.append(f"- `{s.relpath}` (tags: {', '.join(s.tags) or 'none'})")
        if len(plan.unattached) > 25:
            parts.append(f"- ... and {len(plan.unattached) - 25} more")
    return "\n".join(parts) + "\n"


# ---------- Apply --------------------------------------------------------------


def parse_plan(plan_md: str) -> list[HubAttach]:
    """Parse triage-plan.md back into HubAttach records.

    We only return checked attachments. Lines like:
        ## 1. `stub.md` → `hub.md`
        - [x] Attach via tag `climate` (confidence=high)
    """
    out: list[HubAttach] = []
    headings = list(_HEADING_RE.finditer(plan_md))
    for i, m in enumerate(headings):
        block_start = m.end()
        block_end = headings[i + 1].start() if i + 1 < len(headings) else len(plan_md)
        block = plan_md[block_start:block_end]
        cb = _CHECKBOX_RE.search(block)
        if not cb or cb.group(1).strip().lower() != "x":
            continue
        # Pull tag/confidence out of the bullet line for record-keeping
        tag_match = re.search(r"`([^`]+)`\s*\(confidence=(\w+)\)", block)
        out.append(HubAttach(
            stub_relpath=m.group(1).strip(),
            hub_relpath=m.group(2).strip(),
            matched_tag=tag_match.group(1) if tag_match else "",
            confidence=tag_match.group(2) if tag_match else "high",
        ))
    return out


def _insert_see_also(body: str, link_target: str) -> tuple[str, bool]:
    """Add a wikilink to a `## See also` section. Idempotent.

    Returns (new_body, did_insert). If the section already contains a
    wikilink to the same target, returns the body unchanged.
    """
    bullet = f"- [[{link_target}]]\n"
    m = _SEE_ALSO_RE.search(body)
    if m:
        section_start = m.end()
        next_section = re.search(r"^#{1,6}\s+", body[section_start:], re.MULTILINE)
        section_end = section_start + (next_section.start() if next_section else len(body) - section_start)
        section_text = body[section_start:section_end]
        if f"[[{link_target}]]" in section_text:
            return body, False
        before = body[:section_end].rstrip() + "\n"
        after = body[section_end:]
        return before + bullet + ("\n" + after if after and not after.startswith("\n") else after), True
    sep = "" if body.endswith("\n") else "\n"
    new_section = f"{sep}\n## See also\n\n{bullet}"
    return body + new_section, True


def _hub_link_target(hub_relpath: str, all_relpaths: set[str]) -> str:
    """Build the wikilink target. Prefer the basename if unique."""
    name = Path(hub_relpath).stem
    matches = [p for p in all_relpaths if Path(p).stem == name]
    if len(matches) <= 1:
        return name
    # Ambiguous — use full path stem aliased to basename
    qualified = hub_relpath[:-3] if hub_relpath.endswith(".md") else hub_relpath
    return f"{qualified}|{name}"


def _backup_files(vault: Path, files: list[Path]) -> tuple[Path, dict[str, str]]:
    """Tarball the given files into .edge-finder/backups/ with a manifest of
    pre-mutation sha256 sums."""
    backup_dir = vault / ".edge-finder" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    tar_path = backup_dir / f"triage-{ts}.tar"
    manifest_path = backup_dir / f"triage-{ts}.manifest.json"
    manifest: dict[str, str] = {}
    with tarfile.open(tar_path, "w") as tf:
        for f in files:
            try:
                data = f.read_bytes()
            except OSError:
                continue
            manifest[str(f.relative_to(vault))] = hashlib.sha256(data).hexdigest()
            tf.add(f, arcname=str(f.relative_to(vault)))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return tar_path, manifest


def apply_plan(vault: Path, plan_md_path: Path, *, dry_run: bool = False) -> ApplyResult:
    """Read triage-plan.md, mutate stubs for checked attachments, backup."""
    if not plan_md_path.exists():
        return ApplyResult(errors=[f"plan file not found: {plan_md_path}"])

    plan_text = plan_md_path.read_text(encoding="utf-8")
    attachments = parse_plan(plan_text)
    result = ApplyResult(
        proposals_total=plan_text.count("## ") - plan_text.count("## Attachments") - plan_text.count("## Unattached") - 1,
        proposals_checked=len(attachments),
    )
    if not attachments:
        return result

    all_paths = {str(p.relative_to(vault)) for p in vault.rglob("*.md")
                 if not any(part.startswith(".") for part in p.relative_to(vault).parts)}
    affected = [vault / a.stub_relpath for a in attachments]

    if not dry_run:
        result.backup_path, _ = _backup_files(vault, affected)

    for a in attachments:
        stub_path = vault / a.stub_relpath
        if not stub_path.exists():
            result.errors.append(f"stub missing: {a.stub_relpath}")
            continue
        body = stub_path.read_text(encoding="utf-8")
        link_target = _hub_link_target(a.hub_relpath, all_paths)
        new_body, did = _insert_see_also(body, link_target)
        if did:
            result.edges_added += 1
            result.files_touched += 1
            if not dry_run:
                stub_path.write_text(new_body, encoding="utf-8")
    return result


# ---------- Public entrypoint --------------------------------------------------


def run_plan(vault: Path) -> tuple[TriagePlan, Path]:
    """Walk vault, plan triage, write triage-plan.md. Return (plan, path)."""
    notes = list(walk_vault(vault))
    graph, _ = build_graph(notes)
    plan = plan_triage(notes, graph)
    out = vault / "triage-plan.md"
    out.write_text(render_plan_md(plan), encoding="utf-8")
    return plan, out
