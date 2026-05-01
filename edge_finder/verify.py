"""Verify LLM-produced edge proposals before they reach the user.

The LLM (driven by the skill in --judge) writes proposals to
`.edge-finder/proposals-raw.yaml`. This module reads them, runs a
multi-gate validation, and writes survivors to `proposals.md` in the
format the `apply` command expects.

Gates:
  1. Path existence — source and target must be real files in the vault
  2. Edge type — must be declared in the ontology's edge_types list
  3. Confidence — must be `high` or `medium`; `low` is dropped
  4. Verbatim evidence — evidence_quote must appear as a substring in the
     source body OR the target body (whitespace-normalized)

The verbatim gate is the load-bearing one. Cheap models hallucinate
quotes; substring-matching is a hard floor against that.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class RawProposal:
    source: str
    target: str
    edge_type: str
    evidence_quote: str
    confidence: str = ""
    rationale: str = ""


@dataclass
class VerifyResult:
    total: int = 0
    accepted: int = 0
    rejected_path_missing: int = 0
    rejected_edge_type: int = 0
    rejected_confidence: int = 0
    rejected_evidence: int = 0
    rejection_log: list[tuple[str, str, str, str]] = field(default_factory=list)
    accepted_proposals: list[RawProposal] = field(default_factory=list)


_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse whitespace for substring comparison.

    Markdown often has different whitespace layouts (line breaks, indenting)
    than what the LLM cites. Normalizing both sides means an evidence quote
    matches as long as the *content* is verbatim, even if formatting drifts.
    """
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def _read_body(vault: Path, relpath: str) -> str | None:
    p = vault / relpath
    if not p.exists() or not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _allowed_edge_types(ontology: dict) -> set[str]:
    return {e.get("name") for e in ontology.get("edge_types", []) if e.get("name")}


def verify(
    vault: Path,
    raw_yaml_path: Path,
    ontology: dict,
) -> VerifyResult:
    result = VerifyResult()
    if not raw_yaml_path.exists():
        result.rejection_log.append(("--", "--", "raw-missing", str(raw_yaml_path)))
        return result

    data = yaml.safe_load(raw_yaml_path.read_text())
    if not isinstance(data, dict) or "proposals" not in data:
        result.rejection_log.append(("--", "--", "schema", "expected top-level `proposals:` list"))
        return result

    raw_list = data.get("proposals") or []
    allowed_types = _allowed_edge_types(ontology)

    body_cache: dict[str, str] = {}

    for item in raw_list:
        if not isinstance(item, dict):
            continue
        result.total += 1
        prop = RawProposal(
            source=str(item.get("source", "")).strip(),
            target=str(item.get("target", "")).strip(),
            edge_type=str(item.get("edge_type", "")).strip(),
            evidence_quote=str(item.get("evidence_quote", "")).strip(),
            confidence=str(item.get("confidence", "")).strip().lower(),
            rationale=str(item.get("rationale", "")).strip(),
        )

        # Gate 1: paths exist
        src_body = body_cache.get(prop.source)
        if src_body is None:
            src_body = _read_body(vault, prop.source) or ""
            body_cache[prop.source] = src_body
        dst_body = body_cache.get(prop.target)
        if dst_body is None:
            dst_body = _read_body(vault, prop.target) or ""
            body_cache[prop.target] = dst_body

        if not src_body or not dst_body:
            result.rejected_path_missing += 1
            result.rejection_log.append((prop.source, prop.target, "path-missing", ""))
            continue

        # Gate 2: edge_type
        if prop.edge_type not in allowed_types:
            result.rejected_edge_type += 1
            result.rejection_log.append(
                (prop.source, prop.target, "edge-type",
                 f"`{prop.edge_type}` not in ontology"),
            )
            continue

        # Gate 3: confidence
        if prop.confidence not in {"high", "medium"}:
            result.rejected_confidence += 1
            result.rejection_log.append(
                (prop.source, prop.target, "confidence",
                 f"`{prop.confidence}` not in {{high, medium}}"),
            )
            continue

        # Gate 4: verbatim evidence (whitespace-normalized substring)
        norm_quote = _normalize(prop.evidence_quote)
        if not norm_quote:
            result.rejected_evidence += 1
            result.rejection_log.append((prop.source, prop.target, "evidence-empty", ""))
            continue
        if norm_quote not in _normalize(src_body) and norm_quote not in _normalize(dst_body):
            result.rejected_evidence += 1
            result.rejection_log.append(
                (prop.source, prop.target, "evidence-not-found",
                 prop.evidence_quote[:80]),
            )
            continue

        result.accepted += 1
        result.accepted_proposals.append(prop)

    return result


def render_proposals_md(result: VerifyResult) -> str:
    """Render the verified proposals into the format `apply` reads."""
    parts = [
        "# Proposals",
        "",
        f"Generated by `edge-finder verify` — {result.accepted} of {result.total} LLM proposals "
        f"survived the verifiability gate.",
        "",
        "Check the box next to proposals you want applied. Then run:",
        "    edge-finder apply <vault>",
        "",
        "---",
        "",
    ]
    for i, p in enumerate(result.accepted_proposals, 1):
        parts.append(f"## {i}. `{p.source}` ↔ `{p.target}`")
        parts.append("")
        parts.append(f"- [ ] Apply: edge_type=`{p.edge_type}`, confidence={p.confidence}")
        parts.append(f"- Evidence: \"{p.evidence_quote}\"")
        if p.rationale:
            parts.append(f"- Rationale: {p.rationale}")
        parts.append("")
    return "\n".join(parts)


def render_rejection_log(result: VerifyResult) -> str:
    if not result.rejection_log:
        return ""
    parts = ["# Rejected proposals", "", "_These were dropped by the verify gate. Reasons below._", ""]
    counts = {
        "path-missing": result.rejected_path_missing,
        "edge-type": result.rejected_edge_type,
        "confidence": result.rejected_confidence,
        "evidence-not-found": result.rejected_evidence,
    }
    for k, v in counts.items():
        if v:
            parts.append(f"- **{k}**: {v}")
    parts.append("")
    parts.append("---")
    parts.append("")
    for src, dst, reason, detail in result.rejection_log:
        parts.append(f"- [{reason}] `{src}` ↔ `{dst}`" + (f" — {detail}" if detail else ""))
    return "\n".join(parts)
