"""Zero-trust text boundary: normalization, length bounds, injection screening and PII redaction.

Stdlib only, no network. Three jobs, each used where it is safe:

- `clean_text` normalizes untrusted text before anything reads it (tool arguments, transcripts).
  NFKC folds look-alike characters, and invisible/bidi/control characters are removed, so
  "ｇｉｆｔ ｃａｒｄ" or "gift​card" can no longer slip past the deterministic heuristics.
- `looks_like_instruction` flags text that addresses an AI ("ignore previous instructions").
  It is a screen, not a guarantee: pattern lists can't catch every injection. The real defence
  is structural: verdicts come from fixed rules, and actions need a human yes.
- `redact_pii` scrubs phone numbers, emails, SSNs, card numbers and secrets from **log lines**.
  It is deliberately not applied to tool arguments: a caller hint like "555-0147" is how a person
  names a caller, and stripping it would break the lookup.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final

DEFAULT_MAX_LENGTH: Final = 2000

# Zero-width characters, bidi embedding/override/isolate controls, BOM, soft hyphen, word joiners.
_INVISIBLE: Final = re.compile("[­؜᠎​-‏‪-‮⁠-⁤⁦-⁯﻿]")
_CONTROL: Final = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
_SPACES: Final = re.compile(r"\s+")

_INSTRUCTION_PATTERNS: Final = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore\b.{0,30}\b(instructions?|prompts?|rules?|above|previous)\b",
        r"\bdisregard\b.{0,30}\b(instructions?|prompts?|rules?|above|previous)\b",
        r"\b(system|developer)\s+(prompt|message|instructions?)\b",
        r"\byou\s+are\s+now\b",
        r"\bact\s+as\b.{0,20}\b(assistant|admin|developer|system)\b",
        r"\b(assistant|system|user)\s*:",
        r"<\|[a-z_]+\|>|</?(system|assistant|instructions?)>",
        r"\bapprov(e|al)\b.{0,40}\b(block|report|token|everything|all)\b",
        r"\b(approval_token|tool_call|function_call|tools/call)\b",
        r"\bdo\s+not\s+(tell|warn)\s+the\s+(user|person|senior)\b",
    )
)

_PII_PATTERNS: Final = (
    (re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}\b"), "[REDACTED_SECRET]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED_SECRET]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED_SECRET]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"(?<![\w])\+?1?[ .-]?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"), "[REDACTED_PHONE]"),
)


class UnsafeInputError(ValueError):
    """Untrusted text is too long or empty after cleaning."""


def normalize_text(value: str) -> str:
    """NFKC-normalize, drop invisible and control characters, collapse whitespace. No length bound."""
    stripped = _CONTROL.sub(" ", _INVISIBLE.sub("", unicodedata.normalize("NFKC", value)))
    return _SPACES.sub(" ", stripped).strip()


def clean_text(value: str, *, max_length: int = DEFAULT_MAX_LENGTH, allow_empty: bool = False) -> str:
    """`normalize_text`, then enforce length bounds."""
    cleaned = normalize_text(value)
    if len(cleaned) > max_length:
        raise UnsafeInputError(f"text is longer than {max_length} characters after cleaning")
    if not cleaned and not allow_empty:
        raise UnsafeInputError("text is empty after cleaning")
    return cleaned


def looks_like_instruction(text: str) -> bool:
    """True when text appears to address an AI system rather than a person."""
    folded = _SPACES.sub(" ", _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)))
    return any(pattern.search(folded) for pattern in _INSTRUCTION_PATTERNS)


_CARD_CANDIDATE: Final = re.compile(r"\b\d(?:[ -]?\d){12,18}\b")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        n = int(char)
        if index % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _redact_card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    return "[REDACTED_CARD]" if _luhn_valid(digits) else match.group()  # spares timestamps and ids


def redact_pii(text: str) -> str:
    """Replace secrets and personal identifiers in free text, for logs and telemetry only."""
    text = _CARD_CANDIDATE.sub(_redact_card, text)
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text
