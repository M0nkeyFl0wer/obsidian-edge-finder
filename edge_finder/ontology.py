"""Ontology composition and validation.

A vault's effective ontology is composed by:
  1. Starting from `ontology/core.yaml` (universal predicates, base node types)
  2. Adding accepted candidates from the interview
  3. Layering vault-specific tweaks from `<vault>/.edge-finder/ontology.yaml`

The composed result is what edge-finder validates every proposed edge
against before emitting. Edges that violate the schema (subject type
not in predicate.domain, object type not in predicate.range, self-edges,
unknown predicate) are rejected at apply-time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Repo root: edge_finder/ -> obsidian-edge-finder/. Works for editable
# installs. For wheel installs we'd need importlib.resources; mark TODO.
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY_DIR = PACKAGE_ROOT / "ontology"


@dataclass
class Predicate:
    name: str
    description: str
    domain: list[str]
    range: list[str]
    inverse: str | None = None
    multiple: bool = True
    symmetric: bool = False


@dataclass
class NodeType:
    name: str
    description: str
    parent_type: str | None = None
    inferred_from: list[dict] = field(default_factory=list)
    hub_name_patterns: list[str] = field(default_factory=list)
    excluded_folders: list[str] = field(default_factory=list)


@dataclass
class Ontology:
    version: str
    node_types: dict[str, NodeType]
    predicates: dict[str, Predicate]
    validation: dict
    evolution: dict

    def is_subtype(self, child: str, parent: str) -> bool:
        """True if `child` is `parent` or a descendant via parent_type chain."""
        if child == parent:
            return True
        cur = self.node_types.get(child)
        while cur and cur.parent_type:
            if cur.parent_type == parent:
                return True
            cur = self.node_types.get(cur.parent_type)
        return False

    def _type_matches(self, t: str, allowed: list[str]) -> bool:
        if "*" in allowed or "note" in allowed:
            # `*` is an explicit wildcard. `note` is the universal root —
            # everything is a `note` subtype, so `note` in domain/range
            # also acts as a wildcard.
            return True
        return any(self.is_subtype(t, a) for a in allowed)

    def validate_triple(
        self, subject_type: str, predicate: str, object_type: str,
    ) -> tuple[bool, str]:
        if subject_type == object_type and predicate not in {p.name for p in self.predicates.values() if p.symmetric}:
            # Allow self-edges only if the predicate is explicitly symmetric
            # AND the validation rule permits — checked separately.
            pass
        p = self.predicates.get(predicate)
        if not p:
            return False, f"unknown predicate: {predicate}"
        if not self._type_matches(subject_type, p.domain):
            return False, f"subject type {subject_type!r} not in {predicate}.domain {p.domain}"
        if not self._type_matches(object_type, p.range):
            return False, f"object type {object_type!r} not in {predicate}.range {p.range}"
        if self.validation.get("reject_self_edges", True) and subject_type == object_type and not p.symmetric:
            return False, "self-edge rejected"
        return True, "ok"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_core() -> dict:
    """Load the universal core ontology layer."""
    return _load(ONTOLOGY_DIR / "core.yaml")


def load_profile(name: str) -> dict:
    return _load(ONTOLOGY_DIR / "profiles" / f"{name}.yaml")


def list_profiles() -> list[str]:
    return sorted(p.stem for p in (ONTOLOGY_DIR / "profiles").glob("*.yaml"))


def compose(*layers: dict) -> Ontology:
    """Merge layers in order (later layers extend / override earlier).

    Rules:
      - node_types accumulate (later definitions of the same name override)
      - predicates accumulate; a predicate with `overrides_core: true`
        replaces the prior definition rather than merging
      - validation/evolution dicts shallow-merge with later winning
    """
    nt: dict[str, NodeType] = {}
    pr: dict[str, Predicate] = {}
    validation: dict = {}
    evolution: dict = {}

    for layer in layers:
        if not layer:
            continue
        for n in layer.get("node_types") or []:
            nt[n["name"]] = NodeType(
                name=n["name"],
                description=n.get("description", ""),
                parent_type=n.get("parent_type"),
                inferred_from=n.get("inferred_from", []) or [],
                hub_name_patterns=n.get("hub_name_patterns", []) or [],
                excluded_folders=n.get("excluded_folders", []) or [],
            )
        for p in layer.get("predicates") or []:
            pr[p["name"]] = Predicate(
                name=p["name"],
                description=p.get("description", ""),
                domain=p.get("domain", ["*"]) or ["*"],
                range=p.get("range", ["*"]) or ["*"],
                inverse=p.get("inverse"),
                multiple=p.get("multiple", True),
                symmetric=p.get("symmetric", False),
            )
        if "validation" in layer and layer["validation"]:
            validation.update(layer["validation"])
        if "evolution" in layer and layer["evolution"]:
            evolution.update(layer["evolution"])

    return Ontology(
        version="composed",
        node_types=nt,
        predicates=pr,
        validation=validation,
        evolution=evolution,
    )


def load_vault_ontology(vault: Path) -> Ontology:
    """Load and compose the vault's effective ontology.

    Always includes core. Adds the vault layer if `<vault>/.edge-finder/
    ontology.yaml` exists. Profiles are NOT auto-applied — they only
    enter the schema if their pieces were accepted via the interview
    and recorded in the vault layer.
    """
    layers = [load_core()]
    vault_path = vault / ".edge-finder" / "ontology.yaml"
    if vault_path.exists():
        layers.append(_load(vault_path))
    return compose(*layers)


def save_vault_ontology(vault: Path, layer_data: dict) -> Path:
    """Write the vault layer (interview output) to .edge-finder/ontology.yaml."""
    path = vault / ".edge-finder" / "ontology.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(layer_data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return path
