# Stub Corpus Triage — Spec

_Status: draft, 2026-05-01. Driven by a real vault that hit this exactly:
1,175 web-clips + 1,172 bookmark-articles, all URL-stubs, zero outgoing
wikilinks, all of which broke the LLM judgment step in `propose --judge`._

## Problem

A common Obsidian failure mode: the user does a bulk import (Pocket,
Raindrop, Instapaper, Notion, browser bookmarks) and ends up with a
folder of thousands of *structured-but-empty* notes. Each one has
frontmatter (URL, source, tags) and a title — but no body content,
no outgoing wikilinks, and nothing edge-finder can match on.

These corpora **silently break the existing workflow**:

1. `scan` reports them as "orphans" (technically true) but doesn't
   distinguish them from real notes the user just hasn't linked yet.
2. `propose --plan` includes them as candidates, where they flood the
   per-source `k=10` budget with score-1.0 self-duplicates (when two
   import folders hold the same content twice) or with low-signal
   "shared 3 generic web-words" pairs.
3. `propose --judge` correctly skips all of them under the
   precision-over-recall rule, because there's no semantic content
   to write a confident rationale about.
4. The user sees zero web-clip edges and reasonably asks: "isn't
   finding orphans exactly what this skill is supposed to do?"

The skill *is* doing the right thing — refusing to confabulate edges
across a corpus with no body content. But the report doesn't say so,
and the user can't tell the difference between "no edges because
zero-signal" and "no edges because the LLM was lazy."

## What we're building

A new stage in the workflow, between `scan` and `propose`, that:

1. **Detects stub corpora** automatically during `scan`, and surfaces
   them as a distinct gap category (not lumped into "orphans").
2. **Classifies stubs** by type (URL stub / person stub / topic
   stub / project stub / unknown).
3. **Proposes structural attachments** for the cases where a clean
   action exists (e.g., a stub tagged `#climate` in a vault with a
   `Climate Science Kanban.md` hub → attach to the hub).
4. **Defers the rest** with a clear recommendation (re-clip the live
   URLs, archive the dead ones, leave the unclassifiable for human
   review).

This is opt-in via a new subcommand: `edge-finder triage <vault>`.
`scan` just adds detection and reporting. Existing workflows
(`propose`, `verify`, `apply`) are unchanged.

## Detection heuristic

A note is a *stub* if it satisfies all three:

1. **Body is short.** Substantive word count below threshold (default
   30 words) after stripping frontmatter, headings, and template
   boilerplate sections.
2. **Zero outgoing wikilinks.** No `[[...]]` anywhere in the body.
3. **Has at least one structured signal.** One or more of:
   - Frontmatter `url:`, `source:` (matching `https?://`), `link:`
   - A line in the body matching `URL:` or `Source:`
   - A `## Link Information` section
   - A frontmatter `type:` matching `bookmark|reference|contact|stub`

Threshold defaults are tunable via CLI flags. The signal-set is
intentionally conservative — we'd rather miss some stubs than
misclassify a real short note as a stub.

A *stub corpus* is a folder where ≥80% of notes (and ≥50 notes
absolute) match the stub heuristic. Folders below either threshold
just produce per-note flags, not a corpus-level alert.

## Classification

Once a note is flagged as a stub, we classify by the dominant
structured signal:

| Class | Detection signal | Suggested action |
|---|---|---|
| URL stub | frontmatter `url:` or body URL | Re-clip via Web Clipper, or auto-fetch |
| Person stub | `type: person` or `tags: [person]` or `people/` folder | Link to canonical entity if exists |
| Project stub | `type: project` and word count < threshold | Flag for filling, no auto-action |
| Topic stub | Has tags matching existing hub note name | Hub-attach via tag (highest confidence!) |
| Daily stub | matches `\d{4}-\d{2}-\d{2}` filename + empty | Archive if past N days |
| Unknown stub | none of the above | Flag, no auto-action |

The "topic stub" class is the load-bearing one for MVP — it's where
we can produce real edges automatically with no LLM call and no
network access.

## Hub-attach via tag (the MVP mutation)

For each stub with frontmatter tags, we look for an existing note
whose **filename or title matches a tag pattern**. Examples:

| Stub tag | Candidate hubs |
|---|---|
| `climate` | `Climate Science Kanban.md`, `Climate Index.md`, `🌍 Climate.md` |
| `bookmark` | `Bookmarks Overview.md`, `📚 Knowledge Base Index.md` |
| `crypto` | `Crypto Kanban.md`, `Web3 Index.md` |
| `book-review` | `Books.md`, `books/Index.md` |

Match rules (in order):
1. Exact slug match: tag `climate` ↔ note slug `climate`
2. Substring in slug: tag `climate` ↔ `Climate Science Kanban`
3. Substring in title: same logic against the H1

When multiple hubs match, pick the highest-degree one (it's the
better hub by topology). When no hub matches, skip this stub for
this stage — no LLM fallback in MVP.

The hub-attach mutation is a single wikilink in the stub body
under a new `## See also` section. The hub note is **not** modified
(unlike `apply`, which adds reciprocal edges). Rationale: hubs
already have many inbound edges; adding 1,000 more outbound from
the hub would clutter it. The forward edge (stub → hub) is
sufficient for graph traversal and search.

## Workflow

`edge-finder triage <vault>` runs three substages:

1. **Detect.** Walk the vault, classify each stub. Group into
   corpora. No mutation. Output: `.edge-finder/triage-detected.yaml`.
2. **Plan.** For each stub with a clean classification, propose an
   action. Render `triage-plan.md` at vault root with markdown
   checkboxes (same UX as `proposals.md`). Default state:
   - Topic-stub hub-attaches: `[x]` (high confidence, opt-out)
   - URL-stub re-clip suggestions: `[ ]` (informational, opt-in)
   - Everything else: `[ ]`
3. **Apply.** Read `triage-plan.md`, mutate stub bodies for checked
   items only, write tarball backup to
   `.edge-finder/backups/triage-<timestamp>.tar` before any change,
   record manifest with sha256 of pre-mutation files.

`edge-finder triage --apply <vault>` skips the plan-render step if
the user has already reviewed `triage-plan.md` from a previous run.

`edge-finder triage --undo <vault>` restores from the latest backup.

## Report changes

`scan` already writes `vault-report.md`. The triage detection adds a
new section under "Gaps":

```
### 📌 Stub corpora detected (N)

These folders contain mostly URL-only or otherwise-empty notes —
typically from a bulk import. They confuse edge-finding because there
is no body content for the matcher to read. Triage them before
running propose:

    edge-finder triage <vault>

- web-clips/ — 1,175 URL stubs (mean 817B, 0 wikilinks, 100% match
  Pocket-import signature). 810 byte-identical to 07_Bookmarks/articles/.
- 07_Bookmarks/articles/ — 1,172 URL stubs (likely duplicate of
  web-clips/).
- people/ — 47 name-only stubs.
```

The orphan count in the existing islands section is also adjusted
to subtract stub-corpus members, with a footnote explaining the
adjustment ("orphans excluding stub corpora: N").

## What's deferred

These are deliberately out of scope for MVP:

- **URL fetch / readability extraction.** Adds an HTTP dependency
  and the prompt to handle dead links / paywalls / rate-limits.
  Defer to a separate `--fetch` flag in a follow-up.
- **Person-stub canonicalization.** Needs cross-vault name matching
  with handling for honorifics, nicknames, and reading-list authors
  vs collaborators.
- **Daily-stub archival.** Easy to implement but high-stakes —
  defer until the rest is stable, since a wrong default could nuke
  someone's daily-note history.
- **LLM-assisted classification.** A weak classifier could
  hub-attach more stubs (e.g., "this article is climate-related
  even without a #climate tag"). Defer until the deterministic
  rules are battle-tested on more vaults.

## File layout

```
edge_finder/
  stubs.py           # NEW: detection + classification
  triage.py          # NEW: plan + apply for stub mutations
  walker.py          # unchanged
  topology.py        # unchanged
  report.py          # MODIFIED: add stub-corpora section
  surprises.py       # unchanged (stub detection is not a surprise; it's a gap)
  apply.py           # unchanged
  cli.py             # MODIFIED: new `triage` subcommand
  __init__.py        # MODIFIED: export new symbols
```

Tests:

```
tests/
  test_stubs.py      # NEW: detection heuristic, classification
  test_triage.py     # NEW: plan render, apply mutation, undo
```

## Acceptance criteria

1. `edge-finder scan <vault>` on the test vault detects `web-clips/`
   and `07_Bookmarks/articles/` as stub corpora and reports them in
   the new section.
2. `edge-finder triage <vault>` writes a `triage-plan.md` with at
   least one hub-attach proposal per stub that has a tag matching
   a hub-shaped note.
3. `edge-finder triage --apply <vault>` mutates the stub bodies
   (adds `## See also\n- [[<hub>]]\n`) and writes a backup tarball.
4. `edge-finder triage --undo <vault>` restores all mutated stubs
   to their pre-mutation state with hash verification.
5. After triage on the test vault, re-running `edge-finder scan`
   shows a meaningfully reduced orphan count and no longer flags
   the triaged stub corpora as "still orphan."
6. Tests cover: stub detection edge cases (short note that's not a
   stub, long note that has only a URL), classification dispatch,
   hub-name matching with diacritics and emoji, multiple-hub
   tiebreak, idempotency (running apply twice doesn't double-add
   the wikilink).
