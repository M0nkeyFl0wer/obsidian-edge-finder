# Ontology

The ontology is the contract: it defines what subject types,
predicate types, and (subject, predicate, object) triples
edge-finder is allowed to emit. Without this layer the tool can
produce edges, but the result is a topology graph (good for
visualizing) — not a reasoning graph (good for multi-hop queries).

## Interview-first, never auto-applied

Each vault's ontology is composed through a **conversational
interview** that combines scanned content signals with user answers.
**No profile is ever auto-applied** based on a vault-shape trigger
alone — every type and predicate beyond the universal `core.yaml`
enters the schema because the user said yes to a specific question.

See [INTERVIEW.md](INTERVIEW.md) for the interview spec.

## Composition

A vault's effective ontology is built from three layers:

```
1. core.yaml                — universal predicates + base types
                               (note, hub, person, org, concept, project)
                               — these every vault gets

2. interview answers        — user accepts/rejects candidate types
                               and predicates surfaced from
                               profiles/*.yaml during the interview

3. <vault>/.edge-finder/    — composed result, written by the
   ontology.yaml              interview, editable by hand later
```

The `profiles/*.yaml` files are **reference / inspiration**, not
auto-applied. They document what a typical vault of a given shape
might want; the interview pulls candidate offers from them.

The composed result is what edge-finder validates every proposed
edge against. Edges that violate the schema (wrong subject type for
the predicate, wrong object type for the range, self-edges, etc.)
are rejected at apply-time.

## Why this matters

A wikilink without a typed predicate is a navigation hint — Obsidian
shows it in the backlinks panel and the graph view, but you can't
reason over it. With typed predicates, the same wikilinks become
multi-hop-queryable via Dataview:

```dataview
// "Action items assigned to me from meetings about Project X"
TABLE
FROM ""
WHERE type = "action_item"
  AND contains(assigned_to, [[Me]])
  AND any(file(derived_from).discusses, p => p = [[Project X]])
```

That's a 2-hop query: action_item → derived_from → meeting → discusses
→ project. It only works if the edges are typed; otherwise every link
collapses to "vaguely related" and the query has nothing to filter on.

## Ontology evolution

The ontology is not frozen. Edge-finder surfaces evolution proposals
as conversation, never auto-applies:

- When a tag appears as a `discusses::` target ≥10 times without a
  corresponding concept note, the tool proposes promoting it to a
  first-class concept.
- When an organization appears as `same_org::` or `published_in::`
  target ≥5 times without an org note, the tool proposes promoting it.
- Adding a new predicate or node type always requires explicit user
  approval.

## Reference profiles

The interview pulls candidate offers from these. They document
what a vault of a given shape *might* want, not what it gets:

| Profile | Shape it documents | Candidates it offers |
|---|---|---|
| [`granola-meetings`](profiles/granola-meetings.yaml) | Vaults with `## Summary`/`## Attendees`-shaped meeting notes | `meeting`, `action_item`, `decision`, `topic` types; `attended_by`, `follows_up_on`, `decided`, `assigned_to`, `derived_from`, `owned_by` predicates |
| [`bookmark-import`](profiles/bookmark-import.yaml) | Vaults dominated by URL-stub imports (Pocket, Raindrop, etc.) | `bookmark`, `clipping`, `book-review` types; `same_initiative` predicate; widened `same_org` range |

New profiles are welcome — they are reference material, not behavior
changes. Add a YAML under `profiles/` and it becomes available to
the interview's candidate-offer step.

## File formats

The ontology is YAML. The tool reads it via `pyyaml`, no extra
dependencies. Schema validation lives in `edge_finder/ontology.py`
(TODO — not yet implemented; this directory is the spec, the
implementation comes next).
