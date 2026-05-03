"""edge-finder apply — read checked proposals and emit typed predicates.

Accepted `proposals.md` formats during the migration:

New directional format:

    ## 1. `path/to/source.md` → `path/to/target.md`

    - [ ] Apply: predicate=`co_topic`, confidence=high
    - Evidence: "verbatim quote or deterministic evidence"
    - Rationale: one-liner

Legacy format still supported:

    ## 1. `path/to/source.md` ↔ `path/to/target.md`

    - [ ] Apply: edge_type=`discusses`, confidence=high

Apply emits Dataview-style typed predicates using the composed ontology.
The forward triple is always validated. Reverse emission only happens when
the predicate is symmetric or its inverse is explicitly declared in the
ontology.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .ontology import Ontology, load_vault_ontology
from .topology import build_graph
from .triage import find_hubs
from .typed_edges import infer_note_type, insert_typed_edge
from .walker import Note, walk_vault

_HEADING_RE = re.compile(r"^##\s+\d+\.\s+`([^`]+)`\s*(→|↔)\s*`([^`]+)`\s*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(
    r"^\s*-\s+\[(.)\]\s+Apply:\s*(?:predicate|edge_type)=`([^`]+)`(?:,\s*confidence=(\w+))?\s*$",
    re.MULTILINE,
)
_EVIDENCE_RE = re.compile(r"^\s*-\s+Evidence:\s*\"(.+?)\"\s*$", re.MULTILINE)


@dataclass
class Proposal:
    source: str
    target: str
    predicate: str
    confidence: str = ""
    evidence: str = ""
    checked: bool = False
    directional: bool = True


@dataclass
class ApplyResult:
    proposals_total: int = 0
    proposals_checked: int = 0
    edges_added: int = 0
    edges_skipped_existing: int = 0
    files_touched: int = 0
    backup_path: Path | None = None
    errors: list[str] = field(default_factory=list)


def parse_proposals(proposals_md: str) -> list[Proposal]:
    """Parse proposals.md into Proposal records."""
    out: list[Proposal] = []
    headings = list(_HEADING_RE.finditer(proposals_md))
    for i, m in enumerate(headings):
        start = m.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(proposals_md)
        block = proposals_md[start:end]

        cb = _CHECKBOX_RE.search(block)
        if not cb:
            continue
        ev = _EVIDENCE_RE.search(block)

        out.append(Proposal(
            source=m.group(1).strip(),
            target=m.group(3).strip(),
            predicate=cb.group(2).strip(),
            confidence=(cb.group(3) or "").strip(),
            evidence=(ev.group(1).strip() if ev else ""),
            checked=cb.group(1).strip().lower() == "x",
            directional=m.group(2) == "→",
        ))
    return out


def _build_basename_index(vault: Path) -> dict[str, list[str]]:
    """Map basename (without .md) → list of relpaths. Used to disambiguate."""
    index: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(vault, followlinks=False):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in {"node_modules"}]
        for fname in filenames:
            if not fname.endswith(".md"):
                continue
            stem = fname[:-3]
            relpath = str(Path(dirpath, fname).relative_to(vault))
            index.setdefault(stem, []).append(relpath)
    return index


def _wikilink_target(rel_path: str, basename_index: dict[str, list[str]] | None = None) -> str:
    """Render the target form for a `[[wikilink]]`."""
    name = Path(rel_path).name
    if name.endswith(".md"):
        name = name[:-3]
    if basename_index is None:
        return name
    paths = basename_index.get(name, [])
    if len(paths) <= 1:
        return name
    qualified = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return f"{qualified}|{name}"


def _reverse_predicate(predicate: str, ontology: Ontology, *, directional: bool) -> str | None:
    pred = ontology.predicates.get(predicate)
    if not pred:
        return None
    if pred.symmetric:
        return predicate
    if not directional:
        return predicate if predicate in ontology.predicates else None
    if pred.inverse and pred.inverse in ontology.predicates:
        return pred.inverse
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _backup_vault(vault: Path, affected: set[Path]) -> Path:
    backup_dir = vault / ".edge-finder" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"apply-{ts}.tar"
    with tarfile.open(backup_path, "w") as tar:
        for p in affected:
            if p.exists():
                tar.add(p, arcname=str(p.relative_to(vault)))
    return backup_path


def _write_post_apply_manifest(backup_path: Path, vault: Path, affected: set[Path]) -> None:
    manifest: dict[str, str] = {}
    for p in affected:
        if p.exists():
            manifest[str(p.relative_to(vault))] = _sha256(p.read_bytes())
    manifest_path = backup_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def apply_proposals(
    vault: Path,
    proposals_path: Path,
    *,
    dry_run: bool = False,
) -> ApplyResult:
    result = ApplyResult()
    if not proposals_path.exists():
        result.errors.append(f"proposals file not found: {proposals_path}")
        return result

    proposals = parse_proposals(proposals_path.read_text(encoding="utf-8"))
    result.proposals_total = len(proposals)
    checked = [p for p in proposals if p.checked]
    result.proposals_checked = len(checked)
    if not checked:
        return result

    ontology = load_vault_ontology(vault)
    notes = list(walk_vault(vault))
    note_by_path = {n.relpath: n for n in notes}
    graph, _ = build_graph(notes)
    hub_paths = {h.relpath for h in find_hubs(notes, graph)}
    basename_index = _build_basename_index(vault)

    inserts_by_file: dict[Path, list[tuple[str, str]]] = {}
    for p in checked:
        src_path = vault / p.source
        dst_path = vault / p.target
        src_note = note_by_path.get(p.source)
        dst_note = note_by_path.get(p.target)
        if not src_path.exists() or src_note is None:
            result.errors.append(f"source missing: {p.source}")
            continue
        if not dst_path.exists() or dst_note is None:
            result.errors.append(f"target missing: {p.target}")
            continue

        src_type = infer_note_type(src_note, ontology, hub_paths=hub_paths)
        dst_type = infer_note_type(dst_note, ontology, hub_paths=hub_paths)
        valid, reason = ontology.validate_triple(src_type, p.predicate, dst_type)
        if not valid:
            result.errors.append(
                f"schema rejected {p.source} {p.predicate} {p.target}: {reason}"
            )
            continue

        inserts_by_file.setdefault(src_path, []).append(
            (_wikilink_target(p.target, basename_index), p.predicate)
        )

        reverse = _reverse_predicate(p.predicate, ontology, directional=p.directional)
        if not reverse:
            continue
        reverse_valid, reverse_reason = ontology.validate_triple(dst_type, reverse, src_type)
        if not reverse_valid:
            result.errors.append(
                f"schema rejected {p.target} {reverse} {p.source}: {reverse_reason}"
            )
            continue
        inserts_by_file.setdefault(dst_path, []).append(
            (_wikilink_target(p.source, basename_index), reverse)
        )

    planned: list[tuple[Path, str, list[tuple[str, str]]]] = []
    for path, inserts in inserts_by_file.items():
        original = path.read_text(encoding="utf-8")
        body = original
        actual_inserts: list[tuple[str, str]] = []
        for target, predicate in inserts:
            new_body, inserted = insert_typed_edge(body, predicate, target)
            body = new_body
            if inserted:
                actual_inserts.append((target, predicate))
            else:
                result.edges_skipped_existing += 1
        if actual_inserts:
            planned.append((path, body, actual_inserts))

    if dry_run:
        for path, _new_body, inserts in planned:
            for target, predicate in inserts:
                result.edges_added += 1
                print(f"  [dry-run] {path.relative_to(vault)} ← {predicate}:: [[{target}]]")
        return result

    if not planned:
        return result

    affected = {path for path, _, _ in planned}
    result.backup_path = _backup_vault(vault, affected)

    for path, new_body, inserts in planned:
        tmp_path = path.with_suffix(path.suffix + ".edge-finder-tmp")
        tmp_path.write_text(new_body, encoding="utf-8")
        tmp_path.replace(path)
        result.files_touched += 1
        result.edges_added += len(inserts)

    _write_post_apply_manifest(result.backup_path, vault, affected)
    return result


def undo_last_apply(vault: Path, *, force: bool = False) -> tuple[bool, str]:
    backup_dir = vault / ".edge-finder" / "backups"
    if not backup_dir.exists():
        return False, f"no backups found in {backup_dir}"
    backups = sorted(backup_dir.glob("apply-*.tar"))
    if not backups:
        return False, "no apply backups available"
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
                f"refusing to undo — {len(diverged)} files have changed since the last apply:\n"
                f"    {sample}{more}\nRun with --force to overwrite anyway."
            )

    with tarfile.open(latest, "r") as tar:
        tar.extractall(path=vault)
    used_path = latest.with_suffix(".tar.used")
    shutil.move(str(latest), str(used_path))
    if manifest_path.exists():
        manifest_path.replace(manifest_path.with_suffix(".json.used"))
    return True, f"restored from {latest.name}"
