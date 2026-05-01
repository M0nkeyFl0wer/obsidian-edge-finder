"""edge-finder CLI."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from . import _art
from .apply import apply_proposals, undo_last_apply
from .cache import Cache
from .fingerprint import build_fingerprint
from .propose import plan, write_batch
from .propose_holistic import plan_holistic
from .report import render_report
from .shapes import draft_ontology, summarize_shapes
from .stubs import detect_stub_corpora
from .surprises import find_surprises
from .topology import find_gaps
from .triage import apply_plan, run_plan
from .verify import render_proposals_md, render_rejection_log, verify
from .walker import walk_vault


def cmd_scan(vault_path: str) -> int:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        print(f"error: {vault} is not a directory", file=sys.stderr)
        return 2

    print("\n==> ASSESS — read-only inspection (no LLM, no mutations)\n", file=sys.stderr)
    print(f"scanning {vault} ...", file=sys.stderr)
    notes = list(walk_vault(vault))
    if not notes:
        print("error: no markdown notes found", file=sys.stderr)
        return 1
    print(f"  {len(notes)} notes parsed", file=sys.stderr)

    by_path, shape = summarize_shapes(notes)
    print(f"  shape: {dict(shape.by_type)} (granola_share={shape.granola_share:.2f})", file=sys.stderr)

    # Build title->path index used for wikilink classification
    title_to_path: dict[str, str] = {}
    for n in notes:
        title_to_path.setdefault(n.title, n.relpath)
        title_to_path.setdefault(n.path.stem, n.relpath)
        for alias in n.aliases:
            title_to_path.setdefault(alias, n.relpath)

    fingerprints = [
        build_fingerprint(
            n, by_path.get(n.relpath, "generic"),
            type_by_path=by_path, title_to_path=title_to_path,
        )
        for n in notes
    ]
    n_money = sum(1 for fp in fingerprints if fp.money)
    n_deadlines = sum(1 for fp in fingerprints if fp.deadlines)
    n_with_stack = sum(1 for fp in fingerprints if fp.stack)
    n_granola = sum(1 for fp in fingerprints if fp.has_granola_shape)
    n_with_attendees = sum(1 for fp in fingerprints if fp.attendees)
    print(
        f"  dimensions: {n_money} notes mention money, "
        f"{n_deadlines} have deadlines, {n_with_stack} have detectable tech stack",
        file=sys.stderr,
    )
    print(
        f"  granola-shaped: {n_granola} notes; {n_with_attendees} with parsed attendees",
        file=sys.stderr,
    )

    gaps, graph = find_gaps(notes)
    print(
        f"  graph: {gaps.n_nodes} nodes, {gaps.n_edges} edges, "
        f"{gaps.n_islands} islands, {gaps.n_orphans} orphans, "
        f"{gaps.n_articulations} bridges, {gaps.n_loops} centerless loops",
        file=sys.stderr,
    )

    ontology = draft_ontology(shape)

    out_dir = vault / ".edge-finder"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "ontology.draft.yaml").write_text(
        yaml.safe_dump(ontology, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )

    surprises = find_surprises(notes, fingerprints, graph)
    if surprises:
        print(f"  surprises: {len(surprises)} findings worth a look", file=sys.stderr)

    stub_corpora, all_stubs = detect_stub_corpora(notes)
    stub_orphan_overlap = sum(1 for s in all_stubs if s.relpath in set(gaps.orphans))
    if stub_corpora:
        total_stubs = sum(c.n_stubs for c in stub_corpora)
        print(
            f"  stub corpora: {len(stub_corpora)} folder(s), "
            f"{total_stubs} stub notes (overlap with orphans: {stub_orphan_overlap})",
            file=sys.stderr,
        )

    report_md = render_report(
        vault, notes, by_path, shape, gaps, ontology, fingerprints, surprises,
        stub_corpora=stub_corpora, stub_orphan_overlap=stub_orphan_overlap,
    )
    report_path = vault / "vault-report.md"
    report_path.write_text(report_md, encoding="utf-8")

    print(f"\nwrote {report_path}", file=sys.stderr)
    print(f"wrote {out_dir / 'ontology.draft.yaml'}", file=sys.stderr)

    print(_art.render(_art.SCAN_COMPLETE, orphans=gaps.n_orphans), file=sys.stderr)

    print("==> NEXT — review vault-report.md and ontology.draft.yaml.", file=sys.stderr)
    print("    when ready, run: edge-finder propose --plan  (still no LLM)\n", file=sys.stderr)
    return 0


def cmd_propose_plan(
    vault_path: str,
    *,
    mode: str,
    k: int,
    budget: int | None,
    strict: bool,
    min_score: float,
) -> int:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        print(f"error: {vault} is not a directory", file=sys.stderr)
        return 2

    onto_path = vault / ".edge-finder" / "ontology.draft.yaml"
    if not onto_path.exists():
        print(f"error: {onto_path} not found — run `edge-finder scan` first", file=sys.stderr)
        return 1

    notes = list(walk_vault(vault))
    if not notes:
        print("error: no markdown notes found", file=sys.stderr)
        return 1

    by_path, _shape = summarize_shapes(notes)
    ontology = yaml.safe_load(onto_path.read_text())
    cache = Cache.load(vault / ".edge-finder" / "cache")

    if mode == "lean":
        print("\n==> ASSESS — TF-IDF candidate generation (no LLM, lean mode)\n", file=sys.stderr)
        print(f"vault: {vault}", file=sys.stderr)
        print(f"  {len(notes)} notes parsed", file=sys.stderr)
        print(f"  cache: {len(cache.fingerprints)} fingerprints, {len(cache.verdicts)} prior verdicts", file=sys.stderr)
        print("\n==> PLAN — assembling candidate edges\n", file=sys.stderr)

        result = plan(
            notes=notes, by_path_type=by_path, ontology=ontology, cache=cache,
            k=k, min_score=min_score, budget=budget, strict=strict,
        )
        batch_path = vault / "judgment-batch.md"
        write_batch(result, notes, ontology, batch_path)

        print(f"  candidates:           {len(result.candidates)}", file=sys.stderr)
        print(f"  source notes scanned: {result.n_source_notes}", file=sys.stderr)
        print(f"  dropped (thin overlap): {result.n_skipped_thin_overlap}", file=sys.stderr)
        print(f"  estimated tokens:     ~{result.estimated_tokens:,}", file=sys.stderr)
        print(f"\nwrote {batch_path}", file=sys.stderr)
        print(_art.render(_art.PROPOSE_COMPLETE, n=len(result.candidates)), file=sys.stderr)
        print("==> CONFIRM — review judgment-batch.md, delete unwanted candidates,", file=sys.stderr)
        print("    then run: edge-finder propose --judge\n", file=sys.stderr)
        return 0

    # Holistic mode (default)
    print("\n==> ASSESS — building structured fingerprints (holistic mode, no LLM)\n", file=sys.stderr)
    print(f"vault: {vault}", file=sys.stderr)
    print(f"  {len(notes)} notes parsed", file=sys.stderr)

    title_to_path: dict[str, str] = {}
    for n in notes:
        title_to_path.setdefault(n.title, n.relpath)
        title_to_path.setdefault(n.path.stem, n.relpath)
        for alias in n.aliases:
            title_to_path.setdefault(alias, n.relpath)

    fingerprints = [
        build_fingerprint(
            n, by_path.get(n.relpath, "generic"),
            type_by_path=by_path, title_to_path=title_to_path,
        )
        for n in notes
    ]

    print("\n==> PLAN — assembling judgment batch for --judge\n", file=sys.stderr)
    result, prompt_text = plan_holistic(
        notes=notes, fingerprints=fingerprints, ontology=ontology, cache=cache,
    )

    batch_path = vault / "judgment-batch.md"
    batch_path.write_text(prompt_text, encoding="utf-8")

    print(f"  notes in index:           {result.n_notes}", file=sys.stderr)
    print(f"  with structured signal:   {result.n_with_signal}", file=sys.stderr)
    print(f"  sparse (body-snippet only): {result.n_sparse}", file=sys.stderr)
    print(f"  existing edges (context): {result.existing_edges}", file=sys.stderr)
    print(f"  estimated prompt tokens:  ~{result.estimated_tokens:,}", file=sys.stderr)
    if result.fits_in_sonnet:
        fit = "Sonnet 200k context (comfortable)"
    elif result.fits_in_opus:
        fit = "Opus 1M context (only)"
    else:
        fit = "EXCEEDS Opus 1M — chunking needed"
    print(f"  context fit:              {fit}", file=sys.stderr)
    print(f"\nwrote {batch_path}", file=sys.stderr)
    print(_art.render(_art.PROPOSE_COMPLETE, n=result.n_notes), file=sys.stderr)
    print("==> CONFIRM — review judgment-batch.md (this is the EXACT prompt --judge will send),", file=sys.stderr)
    print("    edit / delete sections you don't want, then run: edge-finder propose --judge\n", file=sys.stderr)
    return 0


def cmd_verify(vault_path: str) -> int:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        print(f"error: {vault} is not a directory", file=sys.stderr)
        return 2

    raw_path = vault / ".edge-finder" / "proposals-raw.yaml"
    onto_path = vault / ".edge-finder" / "ontology.draft.yaml"
    if not raw_path.exists():
        print(f"error: {raw_path} not found — the LLM judgment step must produce it first", file=sys.stderr)
        return 1
    if not onto_path.exists():
        print(f"error: {onto_path} not found", file=sys.stderr)
        return 1

    print("\n==> VERIFY — substring-checking LLM evidence quotes (hard gate)\n", file=sys.stderr)
    print(f"vault: {vault}", file=sys.stderr)
    print(f"reading: {raw_path}", file=sys.stderr)

    ontology = yaml.safe_load(onto_path.read_text())
    result = verify(vault, raw_path, ontology)

    print(f"  total proposals:     {result.total}", file=sys.stderr)
    print(f"  accepted:            {result.accepted}", file=sys.stderr)
    print(f"  rejected (path):     {result.rejected_path_missing}", file=sys.stderr)
    print(f"  rejected (edge_type):{result.rejected_edge_type}", file=sys.stderr)
    print(f"  rejected (confidence):{result.rejected_confidence}", file=sys.stderr)
    print(f"  rejected (evidence): {result.rejected_evidence}", file=sys.stderr)

    proposals_path = vault / "proposals.md"
    proposals_path.write_text(render_proposals_md(result), encoding="utf-8")
    print(f"\nwrote {proposals_path}", file=sys.stderr)

    log_text = render_rejection_log(result)
    if log_text:
        log_path = vault / ".edge-finder" / "verify-rejections.md"
        log_path.write_text(log_text, encoding="utf-8")
        print(f"wrote {log_path}", file=sys.stderr)

    print(f"\n==> CONFIRM — review {proposals_path.name}, check the boxes you want,", file=sys.stderr)
    print(f"    then run: edge-finder apply {vault}\n", file=sys.stderr)
    return 0


def cmd_apply(vault_path: str, *, dry_run: bool, undo: bool, force: bool) -> int:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        print(f"error: {vault} is not a directory", file=sys.stderr)
        return 2

    if undo:
        print("\n==> UNDO — restoring vault from last apply backup\n", file=sys.stderr)
        ok, msg = undo_last_apply(vault, force=force)
        print(f"  {msg}", file=sys.stderr)
        return 0 if ok else 1

    proposals_path = vault / "proposals.md"
    phase = "DRY-RUN — preview only, no mutations" if dry_run else "IMPLEMENT — mutating notes"
    print(f"\n==> {phase}\n", file=sys.stderr)
    print(f"vault: {vault}", file=sys.stderr)
    print(f"reading: {proposals_path}", file=sys.stderr)

    result = apply_proposals(vault, proposals_path, dry_run=dry_run)

    print(f"  proposals total:   {result.proposals_total}", file=sys.stderr)
    print(f"  proposals checked: {result.proposals_checked}", file=sys.stderr)
    print(f"  edges added:       {result.edges_added}", file=sys.stderr)
    print(f"  edges skipped (already present): {result.edges_skipped_existing}", file=sys.stderr)
    print(f"  files touched:     {result.files_touched}", file=sys.stderr)
    if result.backup_path:
        print(f"  backup written:    {result.backup_path}", file=sys.stderr)
    for err in result.errors:
        print(f"  ERROR: {err}", file=sys.stderr)

    if not dry_run and result.edges_added > 0:
        print(_art.render(_art.APPLY_COMPLETE, n=result.edges_added), file=sys.stderr)
    return 0 if not result.errors else 1


def cmd_triage(vault_path: str, *, apply: bool, dry_run: bool) -> int:
    vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        print(f"error: {vault} is not a directory", file=sys.stderr)
        return 2

    plan_path = vault / "triage-plan.md"

    if apply:
        phase = "DRY-RUN — preview only, no mutations" if dry_run else "IMPLEMENT — mutating stub notes"
        print(f"\n==> {phase}\n", file=sys.stderr)
        if not plan_path.exists():
            print(f"error: {plan_path} not found — run `edge-finder triage` first", file=sys.stderr)
            return 1
        result = apply_plan(vault, plan_path, dry_run=dry_run)
        print(f"  attachments checked: {result.proposals_checked}", file=sys.stderr)
        print(f"  edges added:         {result.edges_added}", file=sys.stderr)
        print(f"  files touched:       {result.files_touched}", file=sys.stderr)
        if result.backup_path:
            print(f"  backup written:      {result.backup_path}", file=sys.stderr)
        for err in result.errors:
            print(f"  ERROR: {err}", file=sys.stderr)
        return 0 if not result.errors else 1

    print("\n==> ASSESS+PLAN — detecting stubs, matching tags to hub-shaped notes\n", file=sys.stderr)
    print(f"vault: {vault}", file=sys.stderr)
    plan, out = run_plan(vault)
    print(f"  stubs detected:        {plan.n_stubs}", file=sys.stderr)
    print(f"  hub-shaped notes:      {plan.n_hubs}", file=sys.stderr)
    print(f"  attachments proposed:  {len(plan.attachments)}", file=sys.stderr)
    print(f"  unattached stubs:      {len(plan.unattached)}", file=sys.stderr)
    print(f"\nwrote {out}", file=sys.stderr)
    print(f"\n==> CONFIRM — review {out.name}, uncheck anything you don't want,", file=sys.stderr)
    print(f"    then run: edge-finder triage --apply {vault}\n", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge-finder")
    sub = parser.add_subparsers(dest="cmd", required=True)

    scan = sub.add_parser("scan", help="read-only inspection of a vault")
    scan.add_argument("vault", help="path to the Obsidian vault root")

    propose = sub.add_parser("propose", help="generate edge proposals")
    propose.add_argument("vault", help="path to the Obsidian vault root")
    propose.add_argument("--plan", action="store_true",
                         help="assess + plan candidates only (no LLM call)")
    propose.add_argument("--judge", action="store_true",
                         help="run the LLM judgment step (not yet implemented)")
    propose.add_argument("--mode", choices=["holistic", "lean"], default="holistic",
                         help="holistic: structured-index → Claude (default); lean: TF-IDF + per-pair judgment")
    propose.add_argument("-k", type=int, default=10,
                         help="(lean only) max candidates per source note")
    propose.add_argument("--budget", type=int, default=None,
                         help="(lean only) max source notes to process this run")
    propose.add_argument("--strict", action="store_true",
                         help="(lean only) drop candidates whose type pair isn't declared in the ontology")
    propose.add_argument("--min-score", type=float, default=0.08,
                         help="(lean only) minimum cosine similarity to consider")

    verify_p = sub.add_parser("verify", help="validate LLM proposals against vault content")
    verify_p.add_argument("vault", help="path to the Obsidian vault root")

    apply_p = sub.add_parser("apply", help="insert checked proposals as wikilinks into notes")
    apply_p.add_argument("vault", help="path to the Obsidian vault root")
    apply_p.add_argument("--dry-run", action="store_true",
                         help="show what would change without modifying any notes")
    apply_p.add_argument("--undo", action="store_true",
                         help="restore the vault from the most recent apply backup")
    apply_p.add_argument("--force", action="store_true",
                         help="(with --undo) overwrite even if files have been edited since apply")

    triage_p = sub.add_parser("triage", help="propose hub-attach edges for stub notes (bookmark imports etc.)")
    triage_p.add_argument("vault", help="path to the Obsidian vault root")
    triage_p.add_argument("--apply", action="store_true",
                          help="apply the checked attachments from triage-plan.md")
    triage_p.add_argument("--dry-run", action="store_true",
                          help="(with --apply) show what would change without modifying any notes")

    args = parser.parse_args(argv)
    if args.cmd == "scan":
        return cmd_scan(args.vault)
    if args.cmd == "verify":
        return cmd_verify(args.vault)
    if args.cmd == "apply":
        return cmd_apply(args.vault, dry_run=args.dry_run, undo=args.undo, force=args.force)
    if args.cmd == "triage":
        return cmd_triage(args.vault, apply=args.apply, dry_run=args.dry_run)
    if args.cmd == "propose":
        if args.judge:
            print("--judge is not yet implemented; coming after we validate --plan output", file=sys.stderr)
            return 2
        if not args.plan:
            print("specify --plan (no LLM) or --judge (LLM, not yet implemented)", file=sys.stderr)
            return 2
        return cmd_propose_plan(
            args.vault, mode=args.mode, k=args.k, budget=args.budget,
            strict=args.strict, min_score=args.min_score,
        )
    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
