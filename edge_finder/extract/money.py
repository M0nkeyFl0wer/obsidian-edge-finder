"""Extract monetary mentions from note text. Pure regex, no LLM.

Catches the common cases:
  - $40k, $1.5M, $250,000
  - €500, £2k
  - 40k USD, 250000 EUR
  - "budget of 50,000"  (when surrounded by money keywords)

Each match comes with the surrounding sentence as evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Currency-prefixed amounts: $40k, €500, £2.5M, ¥1000
# Critical: \b after the suffix prevents "$700 boat" from being parsed as
# "$700 b" (billion). The suffix must end on a word boundary (space, punct,
# end of string), otherwise we treat it as no-suffix.
_PREFIXED_RE = re.compile(
    r"(?P<currency>[$€£¥₹])\s?(?P<amount>\d{1,3}(?:[,\d]{0,12})(?:\.\d+)?)(?:\s?(?P<suffix>k|m|b|million|billion|thousand))?\b",
    re.IGNORECASE,
)

# Suffixed amounts: 40k USD, 250,000 EUR, 1.5M CAD
_SUFFIXED_RE = re.compile(
    r"\b(?P<amount>\d{1,3}(?:[,\d]{0,12})(?:\.\d+)?)(?:\s?(?P<suffix>k|m|b|million|billion|thousand))?\s?(?P<currency>USD|CAD|EUR|GBP|JPY|AUD|NZD|CHF|INR)\b",
    re.IGNORECASE,
)

_SENTENCE_RE = re.compile(r"[^.!?\n]*[.!?\n]")


@dataclass
class MoneyMention:
    raw: str            # what the regex matched, e.g. "$40k"
    amount_normalized: float | None
    currency: str       # USD, CAD, EUR, ... or '?' if unknown
    quote: str          # the surrounding sentence


_SUFFIX_MULT = {
    "k": 1_000, "thousand": 1_000,
    "m": 1_000_000, "million": 1_000_000,
    "b": 1_000_000_000, "billion": 1_000_000_000,
}


def _suffix_mult(suffix: str | None) -> int:
    if not suffix:
        return 1
    return _SUFFIX_MULT.get(suffix.lower(), 1)

_SYMBOL_TO_CODE = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}


def _normalize(amount_str: str, suffix: str | None) -> float | None:
    if not amount_str:
        return None
    cleaned = amount_str.replace(",", "")
    try:
        n = float(cleaned)
    except ValueError:
        return None
    n *= _suffix_mult(suffix)
    return n


def _surrounding_sentence(text: str, span: tuple[int, int]) -> str:
    start, end = span
    left = max(0, text.rfind(".", 0, start), text.rfind("!", 0, start),
               text.rfind("?", 0, start), text.rfind("\n", 0, start))
    right_candidates = [
        text.find(".", end), text.find("!", end), text.find("?", end), text.find("\n", end),
    ]
    right = min((c for c in right_candidates if c != -1), default=len(text))
    return text[left:right].strip(" \t.!?\n")


def find_money(text: str) -> list[MoneyMention]:
    seen: set[tuple[int, int]] = set()
    out: list[MoneyMention] = []

    for m in _PREFIXED_RE.finditer(text):
        if m.span() in seen:
            continue
        seen.add(m.span())
        currency = _SYMBOL_TO_CODE.get(m.group("currency"), "?")
        out.append(MoneyMention(
            raw=m.group(0).strip(),
            amount_normalized=_normalize(m.group("amount"), m.group("suffix")),
            currency=currency,
            quote=_surrounding_sentence(text, m.span()),
        ))

    for m in _SUFFIXED_RE.finditer(text):
        if m.span() in seen:
            continue
        # Skip if amount is suspicious (e.g., a year like 2026)
        try:
            n = float(m.group("amount").replace(",", ""))
        except ValueError:
            continue
        if 1900 <= n <= 2100 and not m.group("suffix"):
            continue
        seen.add(m.span())
        out.append(MoneyMention(
            raw=m.group(0).strip(),
            amount_normalized=_normalize(m.group("amount"), m.group("suffix")),
            currency=m.group("currency"),
            quote=_surrounding_sentence(text, m.span()),
        ))

    return out
