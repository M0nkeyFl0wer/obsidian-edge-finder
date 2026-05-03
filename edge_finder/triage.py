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
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import networkx as nx

from .stubs import StubInfo, find_stubs
from .typed_edges import infer_note_type, insert_typed_edge
from .ontology import load_vault_ontology
from .topology import build_graph
from .walker import Note, walk_vault

_HUB_NAME_RE = re.compile(r"\b(kanban|index|overview|hub|map|catalog|directory)\b", re.IGNORECASE)
_NORM_RE = re.compile(r"[^a-z0-9]+")
_HEADING_RE = re.compile(r"^##\s+\d+\.\s+`([^`]+)`\s*→\s*`([^`]+)`\s*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s+\[(.)\]\s+Attach\b", re.MULTILINE)


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


_CONTENT_FOLDERS = {"books", "web-clips", "Clippings", "07_Bookmarks", "daily", "Daily", "Templates", "templates"}


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
        parts.append(
            f"- [x] Attach via predicate `topic_hub` from tag `{a.matched_tag}` "
            f"(confidence={a.confidence})"
        )
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
        # Pull tag/confidence out of the bullet line for record-keeping.
        tag_match = re.search(r"from tag `([^`]+)`\s*\(confidence=(\w+)\)", block)
        out.append(HubAttach(
            stub_relpath=m.group(1).strip(),
            hub_relpath=m.group(2).strip(),
            matched_tag=tag_match.group(1) if tag_match else "",
            confidence=tag_match.group(2) if tag_match else "high",
        ))
    return out


def _hub_link_target(hub_relpath: str, all_relpaths: set[str]) -> str:
    """Build the wikilink target. Prefer the basename if unique."""
    name = Path(hub_relpath).stem
    matches = [p for p in all_relpaths if Path(p).stem == name]
    if len(matches) <= 1:
        return name
    # Ambiguous — use full path stem aliased to basename
    qualified = hub_relpath[:-3] if hub_relpath.endswith(".md") else hub_relpath
    return f"{qualified}|{name}"


def _backup_files(vault: Path, files: list[Path]) -> Path:
    """Tarball the given files (pre-mutation contents) into
    .edge-finder/backups/triage-<timestamp>.tar. Returns the tarball path.

    The manifest is written separately, *after* mutations finish, so it
    captures the post-mutation file hashes — that's what `triage --undo`
    checks against to detect manual edits made between apply and undo.
    """
    backup_dir = vault / ".edge-finder" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    tar_path = backup_dir / f"triage-{ts}.tar"
    with tarfile.open(tar_path, "w") as tf:
        for f in files:
            if f.exists():
                tf.add(f, arcname=str(f.relative_to(vault)))
    return tar_path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_post_apply_manifest(backup_path: Path, vault: Path, affected: list[Path]) -> None:
    manifest: dict[str, str] = {}
    for p in affected:
        if p.exists():
            manifest[str(p.relative_to(vault))] = _sha256(p.read_bytes())
    backup_path.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


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

    ontology = load_vault_ontology(vault)
    notes = list(walk_vault(vault))
    note_by_path = {n.relpath: n for n in notes}
    graph, _ = build_graph(notes)
    hub_paths = {h.relpath for h in find_hubs(notes, graph)}
    predicate = "topic_hub"

    all_paths = {str(p.relative_to(vault)) for p in vault.rglob("*.md")
                 if not any(part.startswith(".") for part in p.relative_to(vault).parts)}
    affected = [vault / a.stub_relpath for a in attachments]

    if not dry_run:
        result.backup_path = _backup_files(vault, affected)

    actually_touched: list[Path] = []
    for a in attachments:
        stub_path = vault / a.stub_relpath
        if not stub_path.exists():
            result.errors.append(f"stub missing: {a.stub_relpath}")
            continue
        stub_note = note_by_path.get(a.stub_relpath)
        hub_note = note_by_path.get(a.hub_relpath)
        if stub_note is None or hub_note is None:
            result.errors.append(f"note missing from scan index: {a.stub_relpath} → {a.hub_relpath}")
            continue
        subject_type = infer_note_type(stub_note, ontology, hub_paths=hub_paths)
        object_type = infer_note_type(hub_note, ontology, hub_paths=hub_paths)
        valid, reason = ontology.validate_triple(subject_type, predicate, object_type)
        if not valid:
            result.errors.append(
                f"schema rejected {a.stub_relpath} {predicate} {a.hub_relpath}: {reason}"
            )
            continue
        body = stub_path.read_text(encoding="utf-8")
        link_target = _hub_link_target(a.hub_relpath, all_paths)
        new_body, did = insert_typed_edge(body, predicate, link_target)
        if did:
            result.edges_added += 1
            result.files_touched += 1
            if not dry_run:
                stub_path.write_text(new_body, encoding="utf-8")
                actually_touched.append(stub_path)

    if not dry_run and actually_touched and result.backup_path:
        _write_post_apply_manifest(result.backup_path, vault, actually_touched)
    return result


def undo_last_triage(vault: Path, *, force: bool = False) -> tuple[bool, str]:
    """Restore the vault from the most recent triage-*.tar backup.

    Hash-checks the post-apply manifest against current file state. If any
    file has been edited since the last triage apply, refuses without
    --force so the user doesn't silently lose manual edits.
    """
    backup_dir = vault / ".edge-finder" / "backups"
    if not backup_dir.exists():
        return False, f"no backups found in {backup_dir}"
    backups = sorted(backup_dir.glob("triage-*.tar"))
    if not backups:
        return False, "no triage backups available"
    latest = backups[-1]

    manifest_path = latest.with_suffix(".manifest.json")
    if manifest_path.exists() and not force:
        try:
            expected = json.loads(manifest_path.read_text())
        except json.JSONDecodeError:
            expected = {}
        diverged: list[str] = []
        for relpath, expected_hash in expected.items():
            current = vault / relpath
            if not current.exists():
                diverged.append(f"{relpath} (deleted)")
                continue
            if _sha256(current.read_bytes()) != expected_hash:
                diverged.append(relpath)
        if diverged:
            sample = "\n    ".join(diverged[:5])
            more = f"\n    ... and {len(diverged) - 5} more" if len(diverged) > 5 else ""
            return False, (
                f"refusing to undo — {len(diverged)} files have changed since the last triage:\n"
                f"    {sample}{more}\nRun with --force to overwrite anyway."
            )

    with tarfile.open(latest, "r") as tar:
        tar.extractall(path=vault)
    used_path = latest.with_suffix(".tar.used")
    shutil.move(str(latest), str(used_path))
    if manifest_path.exists():
        manifest_path.replace(manifest_path.with_suffix(".json.used"))
    return True, f"restored from {latest.name}"


# ---------- Public entrypoint --------------------------------------------------


def run_plan(vault: Path) -> tuple[TriagePlan, Path]:
    """Walk vault, plan triage, write triage-plan.md. Return (plan, path)."""
    notes = list(walk_vault(vault))
    graph, _ = build_graph(notes)
    plan = plan_triage(notes, graph)
    out = vault / "triage-plan.md"
    out.write_text(render_plan_md(plan), encoding="utf-8")
    return plan, out


# ---------- --create-hubs: auto-generate hubs from common tags ------------------


_BORING_TAGS = {
    # Tags that show up everywhere and don't make good hub topics.
    "bookmark", "bookmarks", "clipping", "clippings", "stub",
    "web-content", "web-clip", "pocket", "firefox", "flipboard",
    "raindrop", "instapaper", "note", "import", "imported",
    "untagged", "todo", "draft",
}


@dataclass
class HubCreationPlan:
    tag: str
    hub_filename: str        # e.g., "Crypto Hub.md"
    stub_relpaths: list[str] = field(default_factory=list)


def _titlecase_tag(tag: str) -> str:
    """Convert `solar-energy` → `Solar Energy`, `dao` → `DAO`, etc."""
    parts = re.split(r"[-_\s]+", tag)
    out = []
    for p in parts:
        if not p:
            continue
        if p.isupper() or len(p) <= 3:
            out.append(p.upper())
        else:
            out.append(p[:1].upper() + p[1:].lower())
    return " ".join(out)


def plan_hub_creation(
    notes: list[Note],
    graph: nx.Graph,
    *,
    min_tag_count: int = 20,
) -> list[HubCreationPlan]:
    """For each tag in N+ unattached stubs with no existing hub match,
    return a plan to create `{Tag} Hub.md` containing those stubs.

    "Unattached" here means: a stub whose tags don't currently match any
    hub-shaped note. This pairs naturally with `triage --apply` (run apply
    first to land what's matchable, then create-hubs to handle the rest).
    """
    triage = plan_triage(notes, graph)
    hubs = find_hubs(notes, graph)
    existing_hub_norms = {h.norm_slug for h in hubs}

    # Group unattached stubs by their candidate hub tags.
    by_tag: dict[str, list[str]] = {}
    for stub in triage.unattached:
        for tag in stub.candidate_hub_tags:
            tnorm = _norm(tag)
            if not tnorm or tag.lower() in _BORING_TAGS or len(tnorm) < 3:
                continue
            by_tag.setdefault(tag, []).append(stub.relpath)

    hub_creations: list[HubCreationPlan] = []
    for tag, stub_paths in by_tag.items():
        if len(stub_paths) < min_tag_count:
            continue
        if _norm(tag) in existing_hub_norms:
            continue
        # Avoid name collisions with non-hub notes too
        title = _titlecase_tag(tag)
        candidate_filename = f"{title} Hub.md"
        if any(n.relpath == candidate_filename for n in notes):
            continue
        hub_creations.append(HubCreationPlan(
            tag=tag,
            hub_filename=candidate_filename,
            stub_relpaths=sorted(set(stub_paths)),
        ))

    # Bigger hubs first — most useful surface area for the user to review
    hub_creations.sort(key=lambda h: -len(h.stub_relpaths))
    return hub_creations


def _render_hub_body(plan: HubCreationPlan) -> str:
    """Generate the body of a freshly-created hub note."""
    lines = [
        "---",
        "type: hub",
        f"tag: {plan.tag}",
        "auto-generated: true",
        f"created: {datetime.now().date().isoformat()}",
        "tags:",
        "  - hub",
        "  - auto-generated",
        f"  - {plan.tag}",
        "---",
        "",
        f"# {_titlecase_tag(plan.tag)} Hub",
        "",
        f"Auto-generated by `edge-finder triage --create-hubs` from "
        f"{len(plan.stub_relpaths)} notes tagged `#{plan.tag}`. Edit "
        f"the title, description, and groupings as you see fit — this "
        f"file is yours to curate.",
        "",
        "## Notes in this hub",
        "",
    ]
    for relpath in plan.stub_relpaths:
        stub_basename = Path(relpath).stem
        lines.append(f"- [[{stub_basename}]]")
    lines.append("")
    return "\n".join(lines)


def apply_hub_creation(
    vault: Path,
    plans: list[HubCreationPlan],
    *,
    dry_run: bool = False,
) -> ApplyResult:
    """Create the proposed hub notes and attach each stub to its new hub.

    Single backup tarball captures every stub that gets a `## See also`
    bullet AND the (empty) prior state of each new hub file (so undo can
    delete the auto-generated hubs cleanly).
    """
    result = ApplyResult(proposals_total=len(plans), proposals_checked=len(plans))
    if not plans:
        return result

    ontology = load_vault_ontology(vault)
    notes = list(walk_vault(vault))
    note_by_path = {n.relpath: n for n in notes}
    graph, _ = build_graph(notes)
    hub_paths = {h.relpath for h in find_hubs(notes, graph)}
    predicate = "topic_hub"

    all_paths = {str(p.relative_to(vault)) for p in vault.rglob("*.md")
                 if not any(part.startswith(".") for part in p.relative_to(vault).parts)}

    # Filter out plans whose target hub file already exists (race / re-run safety).
    plans = [p for p in plans if not (vault / p.hub_filename).exists()]

    # Files we touch: each existing stub + each new hub file.
    affected_existing: list[Path] = []
    affected_new: list[Path] = []
    for plan in plans:
        affected_new.append(vault / plan.hub_filename)
        for relpath in plan.stub_relpaths:
            stub_path = vault / relpath
            if stub_path.exists():
                affected_existing.append(stub_path)

    if not dry_run:
        # Backup existing files only — new hubs don't exist yet.
        result.backup_path = _backup_files(vault, affected_existing)

    # 1) Create the hub files
    for plan in plans:
        hub_path = vault / plan.hub_filename
        body = _render_hub_body(plan)
        if dry_run:
            print(f"  [dry-run] create {plan.hub_filename} ({len(plan.stub_relpaths)} stubs)")
            continue
        hub_path.write_text(body, encoding="utf-8")
        result.files_touched += 1

    # 2) Add `## See also` to each stub pointing at its new hub
    actually_touched: list[Path] = []
    for plan in plans:
        hub_basename = Path(plan.hub_filename).stem
        for relpath in plan.stub_relpaths:
            stub_path = vault / relpath
            if not stub_path.exists():
                result.errors.append(f"stub missing: {relpath}")
                continue
            stub_note = note_by_path.get(relpath)
            if stub_note is None:
                result.errors.append(f"note missing from scan index: {relpath}")
                continue
            subject_type = infer_note_type(stub_note, ontology, hub_paths=hub_paths)
            valid, reason = ontology.validate_triple(subject_type, predicate, "hub")
            if not valid:
                result.errors.append(
                    f"schema rejected {relpath} {predicate} {plan.hub_filename}: {reason}"
                )
                continue
            body = stub_path.read_text(encoding="utf-8")
            link_target = _hub_link_target(plan.hub_filename, all_paths | {plan.hub_filename})
            new_body, did = insert_typed_edge(body, predicate, link_target if link_target else hub_basename)
            if did:
                result.edges_added += 1
                if not dry_run:
                    stub_path.write_text(new_body, encoding="utf-8")
                    actually_touched.append(stub_path)
                    result.files_touched += 1

    if not dry_run and (actually_touched or affected_new) and result.backup_path:
        # Manifest covers both existing-stub mutations and new-hub files so
        # undo can detect manual edits to either before reverting.
        _write_post_apply_manifest(
            result.backup_path, vault, actually_touched + affected_new
        )
    return result


def run_create_hubs(vault: Path, *, min_tag_count: int = 20, dry_run: bool = False) -> tuple[list[HubCreationPlan], ApplyResult]:
    """End-to-end: detect → plan hubs → apply."""
    notes = list(walk_vault(vault))
    graph, _ = build_graph(notes)
    plans = plan_hub_creation(notes, graph, min_tag_count=min_tag_count)
    result = apply_hub_creation(vault, plans, dry_run=dry_run)
    return plans, result
