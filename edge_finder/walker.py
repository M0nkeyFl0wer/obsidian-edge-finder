"""Walk an Obsidian vault and parse markdown files into Note records.

Frontmatter is parsed with `yaml.safe_load` first; only when that raises
does it fall back to a tiny regex parser. The fallback exists so a
single note with malformed YAML doesn't poison the whole scan.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml

EXCLUDE_DIRS = {".obsidian", ".trash", ".git", "node_modules", ".edge-finder", ".stversions", "Templates"}
EXCLUDE_FILES = {"vault-report.md", "proposals.md", "judgment-batch.md", "triage-plan.md", "interview-prep.md"}

WIKILINK_RE = re.compile(r"\[\[([^\]\|\#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]")
TAG_RE = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")
MENTION_RE = re.compile(r"(?:^|\s)@([A-Za-z][\w-]*)")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Note:
    path: Path
    relpath: str
    title: str
    frontmatter: dict
    body: str
    headings: list[tuple[int, str]] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    word_count: int = 0
    aliases: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return self.path.stem


def _parse_frontmatter_fallback(raw: str) -> dict:
    """Best-effort regex parser for frontmatter that yaml.safe_load rejected.

    Loses block scalars and nested mappings on purpose — those are rare and
    less important than not crashing the whole scan on one bad note.
    """
    fm: dict = {}
    current_key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith(("  ", "\t", "- ")) and current_key:
            val = line.lstrip(" \t-").strip()
            if val.startswith(("'", '"')) and val.endswith(("'", '"')):
                val = val[1:-1]
            existing = fm.get(current_key)
            if isinstance(existing, list):
                existing.append(val)
            elif existing in (None, ""):
                fm[current_key] = [val]
            else:
                fm[current_key] = [existing, val]
        elif ":" in line:
            key, _, val = line.partition(":")
            current_key = key.strip()
            val = val.strip()
            if val.startswith("[") and val.endswith("]"):
                items = [v.strip().strip("'\"") for v in val[1:-1].split(",") if v.strip()]
                fm[current_key] = items
            elif val.startswith(("'", '"')) and val.endswith(("'", '"')):
                fm[current_key] = val[1:-1]
            else:
                fm[current_key] = val
    return fm


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, remaining_body)."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw = m.group(1)
    body = text[m.end():]
    try:
        loaded = yaml.safe_load(raw)
        if isinstance(loaded, dict):
            return loaded, body
    except yaml.YAMLError:
        pass
    return _parse_frontmatter_fallback(raw), body


def parse_note(path: Path, vault_root: Path) -> Note:
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, body = _parse_frontmatter(text)
    title = fm.get("title") or path.stem
    aliases_raw = fm.get("aliases") or fm.get("alias") or []
    if isinstance(aliases_raw, str):
        aliases = [aliases_raw]
    else:
        aliases = list(aliases_raw)

    headings = [(len(m.group(1)), m.group(2).strip()) for m in HEADING_RE.finditer(body)]
    wikilinks = [w.strip() for w in WIKILINK_RE.findall(body)]
    tags = TAG_RE.findall(body)
    mentions = MENTION_RE.findall(body)
    fm_tags = fm.get("tags")
    if isinstance(fm_tags, list):
        tags.extend(t.lstrip("#") for t in fm_tags if isinstance(t, str))
    elif isinstance(fm_tags, str):
        tags.extend(t.lstrip("#") for t in fm_tags.split(",") if t.strip())

    return Note(
        path=path,
        relpath=str(path.relative_to(vault_root)),
        title=str(title),
        frontmatter=fm,
        body=body,
        headings=headings,
        wikilinks=wikilinks,
        tags=sorted(set(tags)),
        mentions=sorted(set(mentions)),
        word_count=len(body.split()),
        aliases=aliases,
    )


def walk_vault(vault_root: Path) -> Iterator[Note]:
    """Yield every parsed Note under vault_root, skipping excluded dirs.

    Uses `os.walk(followlinks=False)` rather than `Path.rglob`. rglob follows
    directory symlinks on some Python versions, which can infinite-loop on
    a vault that contains a symlink to its own parent (or to ~).
    """
    vault_root = vault_root.resolve()
    for dirpath, dirnames, filenames in os.walk(vault_root, followlinks=False):
        # Prune excluded directories in-place so os.walk doesn't descend
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in filenames:
            if not fname.endswith(".md") or fname.startswith("."):
                continue
            if fname in EXCLUDE_FILES:
                continue
            path = Path(dirpath) / fname
            # Skip files that are symlinks pointing outside the vault
            if path.is_symlink():
                try:
                    real = path.resolve()
                    real.relative_to(vault_root)
                except (OSError, ValueError):
                    continue
            try:
                yield parse_note(path, vault_root)
            except (OSError, UnicodeDecodeError):
                continue


def _should_skip_dir(name: str) -> bool:
    return name in EXCLUDE_DIRS or name.startswith(".") or name.endswith(".backup")
