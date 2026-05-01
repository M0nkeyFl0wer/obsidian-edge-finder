"""Terminal flourishes. Small enough to make people smile, small enough to ignore."""

SCAN_COMPLETE = r"""
       ·     ·   ·       ·
    ·       ·       ·
       ·  {orphans:>4} lonely notes  ·
    ·     ·       ·    ·
       ·       ·
            ·
"""

PROPOSE_COMPLETE = r"""
       ✦         ✦
        ╲       ╱
         ✦ ─ ✦
        ╱       ╲
       ✦         ✦
   {n} notes in batch — review judgment-batch.md before firing --judge
"""

APPLY_COMPLETE = r"""
       ✦───✦───✦
       │ ╲ │ ╱ │
       ✦───✦───✦
       │ ╱ │ ╲ │       {n} new edges woven
       ✦───✦───✦       your vault is denser today
"""


def render(template: str, **kwargs) -> str:
    return template.format(**kwargs)
"""Keep this file tiny. If it grows past 30 lines, the joke stopped being funny."""
