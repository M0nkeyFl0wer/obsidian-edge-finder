# Ontology Interview — spec

The interview is how a vault gets its ontology. Edge-finder reads
the user's actual content, asks targeted questions, and composes a
custom `<vault>/.edge-finder/ontology.yaml` from the answers. **No
profile is ever auto-applied** — every type and predicate beyond
the universal `core.yaml` enters the vault's ontology because the
user said yes to a specific question.

## Why interview-first

The two failure modes this avoids:

1. **Wrong defaults.** A pre-baked "bookmark profile" that
   auto-applies because 40% of notes look like URL stubs forces a
   schema on a user who may have wanted those notes deleted, not
   typed. Auto-application loses information about user intent.
2. **One-shot ontology authoring.** Asking a user to write an
   ontology before using the tool is a wall — most users don't
   know what they want until they see what's in their vault.

The interview makes ontology development *organic* — the user
reacts to what the tool surfaces, and the schema falls out of
that conversation.

## When the interview runs

- **First scan of a vault:** edge-finder detects the vault has no
  `<vault>/.edge-finder/ontology.yaml` and proposes running the
  interview before `triage` or `propose`.
- **On demand:** `edge-finder ontology <vault>` (or
  `edge-finder ontology --revise <vault>` to extend an existing
  ontology with new questions about content added since).
- **After significant scope drift:** when a re-scan shows the vault
  now has 2x the notes since last interview, suggest running again.

## What it asks

Questions are derived from the scan output. The skill that runs
the interview is the LLM partner; the deterministic Python side
just surfaces the structural signals it should ask about. Examples:

### Shape-based questions

| Signal in scan | Question |
|---|---|
| `granola_share ≥ 0.20` | "X% of your notes match the Granola meeting shape (`## Summary`, `## Attendees`). Want `meeting`, `attendee`, `action_item`, and `decision` to be first-class types? Want predicates like `attended_by`, `follows_up_on`, `assigned_to`?" |
| `stub_share ≥ 0.20` | "You have N URL-stub notes (no body content) in folders X, Y. Are these primary content (process them), import scrap (delete), or archive (exclude from edge-finding)?" |
| `book-review` notes present | "I see N book-review notes. Should `book-review` be a node type, with `authored_by`, `published_in`, and `cites` predicates available?" |
| `mastodon_post` or social-media folder | "Captured social posts present. Should they get their own type, or roll up under `note`?" |

### Tag-based questions

For each tag occurring on N+ notes (default N=10):
- "Tag `#X` appears on N notes. Should it be:
  - (a) A `concept` (queryable as `discusses:: [[X]]`)
  - (b) A `hub` (queryable as `topic_hub:: [[X]]`, listed in graph view)
  - (c) Just a tag (no upgrade)?"

### Author / org questions

When N+ wikilinks appear in `author:` frontmatter:
- "I see N distinct author wikilinks across your notes. Should
  `author` be a `person` type with `authored_by` edges?"

When N+ same-domain bookmarks appear:
- "N notes are from `domain.com`. Should there be an org node
  `[[domain.com]]` with `published_in` edges?"

### Folder convention questions

For each top-level folder with ≥20 notes:
- "Folder `<name>/` has N notes — what is this folder for? (tab-completes from
  detected types: project / clipping / book-review / etc.)"

## Composition algorithm

```
composed_ontology = core.yaml
for each user_answer in interview:
    if answer is "yes, type X":
        composed_ontology.node_types += X
    if answer is "yes, predicate P":
        composed_ontology.predicates += P
    if answer is "yes, tag T → hub":
        composed_ontology.hub_promotions += T
    if answer is "exclude folder F":
        composed_ontology.excluded_folders += F
write composed_ontology to <vault>/.edge-finder/ontology.yaml
```

The `profiles/*.yaml` files are the **source of candidate types and
predicates** for the offers. When the interview detects Granola
shape, it pulls candidate predicates from
`profiles/granola-meetings.yaml` and offers each one individually
("Want `attended_by`?" "Want `follows_up_on`?"). The user accepts
or rejects each.

## After the interview

Once `<vault>/.edge-finder/ontology.yaml` exists:

- `triage` emits typed predicates (e.g., `topic_hub:: [[X]]` instead
  of bare `## See also` bullets) according to the composed schema.
- `propose --plan` ranks edge candidates by predicate type and
  validates them against domain/range before writing
  `judgment-batch.md`.
- `apply` rejects any proposal whose `(subject_type, predicate,
  object_type)` triple isn't in the schema.
- Multi-hop Dataview queries become possible because edges carry
  meaningful predicates.

## Re-running

A vault's ontology evolves. `edge-finder ontology --revise`:

- Re-scans the vault.
- Surfaces new signals not present at last interview (new tags
  with high frequency, new folder structures, new frontmatter
  type values).
- Asks only the *new* questions — doesn't re-ask things the user
  already decided.
- Appends additions to the existing `<vault>/.edge-finder/ontology.yaml`
  (preserves the user's prior decisions).

## Status

This is a spec for the next implementation phase. As of now
(2026-05-02) edge-finder has:

- ✅ The `core.yaml` schema (universal predicates and base types)
- ✅ Reference profiles in `profiles/` (granola-meetings, bookmark-import)
- ❌ The interview subcommand (`edge-finder ontology`)
- ❌ Ontology composition logic
- ❌ Edge-validation against the composed schema in `triage`/`apply`

The interview is the bridge between today's untyped wikilinks and
tomorrow's reasoning-capable typed graph.
