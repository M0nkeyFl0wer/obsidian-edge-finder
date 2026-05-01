"""Detect tech stack / programming languages mentioned in a note.

Three signals, in order of reliability:
  1. Fenced code block language tags (```python, ```ts) — almost never wrong
  2. Frontmatter `stack:` / `tech:` / `language:` fields
  3. Token mentions in prose (case-aware) — most prone to false positives,
     so we require the canonical case (e.g. "React" not "react") and
     gate on word boundaries.

The list is intentionally web-dev-leaning. Customizable via tech.yaml in
the future; hardcoded for v1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..walker import Note

# Canonical case is the form we look for. Synonyms map to the canonical form.
_TECH_CANONICAL: dict[str, list[str]] = {
    # Languages (also the most common code-block tags)
    "Python":      ["python", "py"],
    "JavaScript":  ["javascript", "js"],
    "TypeScript":  ["typescript", "ts", "tsx"],
    "Ruby":        ["ruby", "rb"],
    "Go":          ["go", "golang"],
    "Rust":        ["rust", "rs"],
    "Java":        ["java"],
    "Kotlin":      ["kotlin", "kt"],
    "Swift":       ["swift"],
    "C#":          ["csharp", "c#"],
    "PHP":         ["php"],
    "Bash":        ["bash", "sh", "zsh"],
    "SQL":         ["sql"],
    # Frontend frameworks
    "React":       ["react", "reactjs"],
    "Next.js":     ["nextjs", "next.js"],
    "Vue":         ["vue", "vuejs"],
    "Svelte":      ["svelte", "sveltekit"],
    "Astro":       ["astro"],
    "Tailwind":    ["tailwind", "tailwindcss"],
    # Backend
    "Node.js":     ["nodejs", "node.js"],
    "Express":     ["express"],
    "FastAPI":     ["fastapi"],
    "Django":      ["django"],
    "Rails":       ["rails", "ruby on rails"],
    "Laravel":     ["laravel"],
    "Flask":       ["flask"],
    # Databases
    "Postgres":    ["postgres", "postgresql"],
    "MySQL":       ["mysql"],
    "MongoDB":     ["mongodb", "mongo"],
    "Redis":       ["redis"],
    "SQLite":      ["sqlite"],
    "Supabase":    ["supabase"],
    "Firebase":    ["firebase"],
    "DuckDB":      ["duckdb"],
    # Infra
    "Docker":      ["docker"],
    "Kubernetes":  ["kubernetes", "k8s"],
    "AWS":         ["aws"],
    "Vercel":      ["vercel"],
    "Netlify":     ["netlify"],
    "Cloudflare":  ["cloudflare"],
}

# Reverse index for fast matching
_LOOKUP: dict[str, str] = {}
for canonical, aliases in _TECH_CANONICAL.items():
    for a in aliases:
        _LOOKUP[a.lower()] = canonical
    _LOOKUP[canonical.lower()] = canonical

_FENCE_RE = re.compile(r"```\s*([A-Za-z0-9+#.\-]+)")
_PROSE_TOKEN_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9.+#-]{1,15}\b")


@dataclass
class StackMention:
    name: str        # canonical form, e.g. "Next.js"
    source: str      # "code-fence" | "frontmatter" | "prose"
    count: int = 1


def find_stack(note: Note) -> list[StackMention]:
    found: dict[str, StackMention] = {}

    def _add(name: str, source: str) -> None:
        if name in found:
            found[name].count += 1
        else:
            found[name] = StackMention(name=name, source=source)

    # 1. Code fences — most reliable
    for m in _FENCE_RE.finditer(note.body):
        lang = m.group(1).lower()
        canonical = _LOOKUP.get(lang)
        if canonical:
            _add(canonical, "code-fence")

    # 2. Frontmatter
    for key in ("stack", "tech", "language", "languages"):
        val = note.frontmatter.get(key)
        if not val:
            continue
        items = val if isinstance(val, list) else [val]
        for item in items:
            if not isinstance(item, str):
                continue
            canonical = _LOOKUP.get(item.strip().lower())
            if canonical:
                _add(canonical, "frontmatter")

    # 3. Prose mentions — case-aware to reduce false positives
    # Walk tokens in original case; only count if exact canonical case matches.
    for tok in _PROSE_TOKEN_RE.findall(note.body):
        canonical = _LOOKUP.get(tok.lower())
        if not canonical:
            continue
        # Require the canonical form's case (or all-caps proper nouns)
        # to avoid e.g. "go" the verb being treated as Go the language.
        if tok == canonical or tok in _TECH_CANONICAL.get(canonical, []):
            _add(canonical, "prose")
        elif tok.lower() == canonical.lower() and canonical[0].isupper():
            # "React" matches "React" but not "react" — strict case for short
            # ambiguous names (Go, Vue, Rust) reduces false positives in prose.
            if canonical in {"Go", "Vue", "Rust", "Astro", "Next.js"}:
                continue
            _add(canonical, "prose")

    return list(found.values())
