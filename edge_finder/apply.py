"""edge-finder apply — read checked proposals, insert wikilinks into notes.

Format expected in proposals.md (this is also what --judge will produce):

    ## 1. `path/to/source.md` ↔ `path/to/target.md`

    - [ ] Apply: edge_type=`discusses`, confidence=high
    - Evidence: "verbatim quote from one of the bodies"
    - Rationale: one-liner

A checked box means "do it"; unchecked means skip. Apply inserts a
single bullet under a `## Related` section in BOTH source and target
(edges are undirected as far as the vault is concerned).

Mutations are atomic per file and a tarball backup is written to
`.edge-finder/backups/apply-<timestamp>.tar` before any file is touched.
`apply --undo` extracts the most recent backup over the vault, with a
hash check that aborts when the user has edited any of those files
manually since the apply (use --force to override).
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

_HEADING_RE = re.compile(r"^##\s+\d+\.\s+`([^`]+)`\s*↔\s*`([^`]+)`\s*$", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*-\s+\[(.)\]\s+Apply:\s*edge_type=`([^`]+)`(?:,\s*confidence=(\w+))?\s*$", re.MULTILINE)
_EVIDENCE_RE = re.compile(r"^\s*-\s+Evidence:\s*\"(.+?)\"\s*$", re.MULTILINE)
_RELATED_HEADING_RE = re.compile(r"^##\s+Related\s*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([^\]\|\#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")


@dataclass
class Proposal:
    source: str           # relpath
    target: str           # relpath
    edge_type: str
    confidence: str = ""
    evidence: str = ""
    checked: bool = False


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
            target=m.group(2).strip(),
            edge_type=cb.group(2).strip(),
            confidence=(cb.group(3) or "").strip(),
            evidence=(ev.group(1).strip() if ev else ""),
            checked=cb.group(1).strip().lower() == "x",
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
    """Render the target form for a `[[wikilink]]`.

    If the basename is unique in the vault, use it (`[[Foo]]`). If multiple
    notes share the basename, qualify with the path so Obsidian doesn't
    silently link to the wrong one (`[[Folder/Subfolder/Foo|Foo]]`).
    """
    name = Path(rel_path).name
    if name.endswith(".md"):
        name = name[:-3]
    if basename_index is None:
        return name
    paths = basename_index.get(name, [])
    if len(paths) <= 1:
        return name
    # Ambiguous — use the full path stem (without .md) and alias to the basename
    qualified = rel_path[:-3] if rel_path.endswith(".md") else rel_path
    return f"{qualified}|{name}"


def _existing_wikilinks_in_section(section_text: str) -> set[str]:
    return {m.group(1).strip().lower() for m in _WIKILINK_RE.finditer(section_text)}


def _insert_related_link(body: str, link_target: str, edge_type: str) -> tuple[str, bool]:
    """Insert a related-link bullet. Returns (new_body, did_insert)."""
    bullet = f"- [[{link_target}]] — {edge_type}\n"

    m = _RELATED_HEADING_RE.search(body)
    if m:
        # Find the body of the Related section: from end of heading line to next ## or EOF
        section_start = m.end()
        next_section = re.search(r"^#{1,6}\s+", body[section_start:], re.MULTILINE)
        section_end = section_start + (next_section.start() if next_section else len(body) - section_start)
        section_text = body[section_start:section_end]

        # Skip if a wikilink to this target already exists in the section
        existing = _existing_wikilinks_in_section(section_text)
        if link_target.lower() in existing:
            return body, False

        # Insert at end of section, before next heading / EOF
        # Ensure section ends with a newline before our insert
        before = body[:section_end].rstrip() + "\n"
        after = body[section_end:]
        return before + bullet + ("\n" + after if after and not after.startswith("\n") else after), True

    # No Related section yet — append one at end of file
    sep = "" if body.endswith("\n") else "\n"
    new_section = f"{sep}\n## Related\n\n{bullet}"
    return body + new_section, True


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
    """Write a sidecar JSON: relpath → sha256 of the file *after* apply.

    On --undo we re-hash every file and abort if any has changed since,
    so users don't silently lose manual edits made between apply and undo.
    """
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

    # Build a basename index once so we can disambiguate links into folders
    # when two notes share a filename.
    basename_index = _build_basename_index(vault)

    # Group inserts by file: each proposal mutates BOTH source and target
    # (related-link is undirected from a vault-browsing point of view).
    inserts_by_file: dict[Path, list[tuple[str, str]]] = {}
    for p in checked:
        src_path = vault / p.source
        dst_path = vault / p.target
        if not src_path.exists():
            result.errors.append(f"source missing: {p.source}")
            continue
        if not dst_path.exists():
            result.errors.append(f"target missing: {p.target}")
            continue
        inserts_by_file.setdefault(src_path, []).append(
            (_wikilink_target(p.target, basename_index), p.edge_type)
        )
        inserts_by_file.setdefault(dst_path, []).append(
            (_wikilink_target(p.source, basename_index), p.edge_type)
        )

    # Plan first — compute what would actually change so we only back up files
    # that will mutate. Re-running idempotently leaves no backup behind.
    planned: list[tuple[Path, str, list[tuple[str, str]]]] = []
    for path, inserts in inserts_by_file.items():
        original = path.read_text(encoding="utf-8")
        body = original
        actual_inserts: list[tuple[str, str]] = []
        for target, etype in inserts:
            new_body, inserted = _insert_related_link(body, target, etype)
            body = new_body
            if inserted:
                actual_inserts.append((target, etype))
            else:
                result.edges_skipped_existing += 1
        if actual_inserts:
            planned.append((path, body, actual_inserts))

    if dry_run:
        for path, _new_body, inserts in planned:
            for target, etype in inserts:
                result.edges_added += 1
                print(f"  [dry-run] {path.relative_to(vault)} ← [[{target}]] ({etype})")
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

    # Manifest written AFTER mutation so it captures the post-apply state.
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

    # Hash-check current state against the post-apply manifest. If any file
    # has been edited since the apply, refuse without --force so we don't
    # silently destroy the user's manual edits.
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
