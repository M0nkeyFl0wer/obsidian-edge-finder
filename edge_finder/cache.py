"""Fingerprint + verdict cache.

The whole point of this cache is the "repeatedly" property — after the first
run, only changed notes incur cost. Two stores:

  fingerprints.json   path → {sha256, mtime, summary, type}
  verdicts.jsonl      one line per (src_sha, dst_sha, ontology_version) judgment

Both are JSON for git-diffability and human inspection. JSONL for verdicts
because it's append-only and survives partial writes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .walker import Note


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()


def fingerprint(note: Note) -> str:
    """Content fingerprint — stable across mtime changes that don't change body."""
    return sha256_text(note.body)


@dataclass
class Fingerprint:
    path: str
    sha256: str
    mtime: float
    note_type: str = "generic"
    summary: str = ""


@dataclass
class Verdict:
    src_sha: str
    dst_sha: str
    ontology_version: str
    decision: str   # "accept" | "reject" | "uncertain"
    edge_type: str = ""
    evidence: str = ""
    src_path: str = ""
    dst_path: str = ""


@dataclass
class Cache:
    cache_dir: Path
    fingerprints: dict[str, Fingerprint] = field(default_factory=dict)
    verdicts: dict[tuple[str, str, str], Verdict] = field(default_factory=dict)

    @classmethod
    def load(cls, cache_dir: Path) -> "Cache":
        cache_dir.mkdir(parents=True, exist_ok=True)
        c = cls(cache_dir=cache_dir)
        fp_path = cache_dir / "fingerprints.json"
        if fp_path.exists():
            data = json.loads(fp_path.read_text())
            c.fingerprints = {k: Fingerprint(**v) for k, v in data.items()}
        v_path = cache_dir / "verdicts.jsonl"
        if v_path.exists():
            for line in v_path.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                    v = Verdict(**rec)
                    c.verdicts[(v.src_sha, v.dst_sha, v.ontology_version)] = v
                except (json.JSONDecodeError, TypeError):
                    continue
        return c

    def save_fingerprints(self) -> None:
        fp_path = self.cache_dir / "fingerprints.json"
        data = {k: asdict(v) for k, v in self.fingerprints.items()}
        fp_path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def append_verdict(self, v: Verdict) -> None:
        self.verdicts[(v.src_sha, v.dst_sha, v.ontology_version)] = v
        v_path = self.cache_dir / "verdicts.jsonl"
        with v_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(v)) + "\n")

    def has_verdict(self, src_sha: str, dst_sha: str, ontology_version: str) -> bool:
        # Symmetric: a verdict on (A,B) covers (B,A) for undirected purposes
        if (src_sha, dst_sha, ontology_version) in self.verdicts:
            return True
        return (dst_sha, src_sha, ontology_version) in self.verdicts


def refresh_fingerprints(cache: Cache, notes: list[Note], by_type: dict[str, str]) -> tuple[int, int]:
    """Update cache.fingerprints from current notes. Returns (changed, total)."""
    changed = 0
    new_fps: dict[str, Fingerprint] = {}
    for n in notes:
        sha = fingerprint(n)
        prev = cache.fingerprints.get(n.relpath)
        if prev is None or prev.sha256 != sha:
            changed += 1
        new_fps[n.relpath] = Fingerprint(
            path=n.relpath,
            sha256=sha,
            mtime=n.path.stat().st_mtime,
            note_type=by_type.get(n.relpath, "generic"),
            summary=prev.summary if (prev and prev.sha256 == sha) else "",
        )
    cache.fingerprints = new_fps
    return changed, len(notes)


def ontology_version(ontology: dict) -> str:
    return sha256_text(json.dumps(ontology, sort_keys=True))[:12]
