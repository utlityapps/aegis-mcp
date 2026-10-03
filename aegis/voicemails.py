"""Voicemail fixtures and deterministic caller lookup. Stdlib only, no network."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "voicemails"
VOICEMAIL_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
REQUIRED_FIELDS = ("voicemail_id", "received_at", "caller_number", "transcript")
MAX_CALLER_NAME = 80  # outputSchema caller_label maxLength
MAX_CALLER_NUMBER = 32  # outputSchema caller_number maxLength


class FixtureError(ValueError):
    """A fixture file is missing, malformed, or duplicates another voicemail id."""


@dataclass(frozen=True, slots=True)
class Voicemail:
    voicemail_id: str
    caller_number: str
    caller_name: str | None
    received_at: datetime
    transcript: str


@dataclass(frozen=True, slots=True)
class CallerGroup:
    """A fixed synonym group: hint words on one side, words to find in the voicemail on the other."""

    name: str
    hint_terms: tuple[str, ...]
    match_terms: tuple[str, ...]


CALLER_GROUPS: tuple[CallerGroup, ...] = (
    CallerGroup(
        "irs",
        ("irs", "internal revenue", "internal revenue service", "tax", "taxes", "tax office"),
        ("irs", "internal revenue", "internal revenue service"),
    ),
    CallerGroup(
        "social_security",
        ("ssa", "social security", "social security administration", "social security office"),
        ("ssa", "social security administration", "social security office"),
    ),
    CallerGroup("medicare", ("medicare",), ("medicare",)),
    CallerGroup("bank", ("bank", "fraud department"), ("bank",)),
    CallerGroup(
        "grandchild",
        ("grandson", "granddaughter", "grandchild", "grandchildren", "grandkid", "grandkids"),
        ("grandma", "grandpa", "grandmother", "grandfather", "granny"),
    ),
    CallerGroup("pharmacy", ("pharmacy", "drugstore", "prescription"), ("pharmacy",)),
    CallerGroup("doctor", ("doctor", "dr", "doctors office", "physician", "clinic"), ("doctor", "dr")),
    CallerGroup("child", ("daughter", "son"), ("hi mom", "hi dad")),
)

_STOP_WORDS = frozenset(
    {"the", "my", "a", "an", "from", "of", "voicemail", "message", "call", "caller", "that", "who", "just", "got", "i", "me"}
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_NON_DIGIT = re.compile(r"\D+")


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def collapse_spelled_acronyms(normalized: str) -> str:
    """Join runs of single letters, as speech-to-text writes acronyms: 'the i r s' -> 'the irs'."""
    merged: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if len(run) > 1:
            merged.append("".join(run))
        else:
            merged.extend(run)
        run.clear()

    for token in normalized.split():
        if len(token) == 1 and token.isalpha():
            run.append(token)
        else:
            flush()
            merged.append(token)
    flush()
    return " ".join(merged)


def digits_of(text: str) -> str:
    return _NON_DIGIT.sub("", text)


def _has_phrase(haystack: str, phrase: str) -> bool:
    return f" {phrase} " in f" {haystack} "


def matches_hint(voicemail: Voicemail, hint: str) -> bool:
    """Deterministically decide whether a spoken caller description refers to this voicemail."""
    hint_norm = collapse_spelled_acronyms(normalize(hint))
    haystack = f"{normalize(voicemail.caller_name or '')} {normalize(voicemail.transcript)}"

    groups = [g for g in CALLER_GROUPS if any(_has_phrase(hint_norm, term) for term in g.hint_terms)]
    if groups:
        return any(_has_phrase(haystack, term) for g in groups for term in g.match_terms)

    hint_digits = digits_of(hint)
    if len(hint_digits) >= 4:
        return hint_digits in digits_of(voicemail.caller_number)

    words = [w for w in hint_norm.split() if w not in _STOP_WORDS and len(w) >= 2]
    return bool(words) and all(_has_phrase(haystack, w) for w in words)


def _parse_fixture(path: Path) -> Voicemail:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureError(f"{path.name}: cannot read fixture: {exc}") from exc
    if not isinstance(data, dict):
        raise FixtureError(f"{path.name}: fixture must be a JSON object")
    missing = [field for field in REQUIRED_FIELDS if not isinstance(data.get(field), str) or not data[field]]
    if missing:
        raise FixtureError(f"{path.name}: missing or empty fields: {', '.join(missing)}")
    if not VOICEMAIL_ID_PATTERN.fullmatch(data["voicemail_id"]):
        raise FixtureError(f"{path.name}: voicemail_id {data['voicemail_id']!r} does not match the id pattern")
    caller_name = data.get("caller_name")
    if caller_name is not None and (not isinstance(caller_name, str) or not caller_name.strip()):
        raise FixtureError(f"{path.name}: caller_name must be a non-empty string or null")
    if caller_name is not None and len(caller_name) > MAX_CALLER_NAME:
        raise FixtureError(f"{path.name}: caller_name is longer than {MAX_CALLER_NAME} characters")
    if len(data["caller_number"]) > MAX_CALLER_NUMBER or not digits_of(data["caller_number"]):
        raise FixtureError(f"{path.name}: caller_number must contain digits and be at most {MAX_CALLER_NUMBER} characters")
    try:
        received_at = datetime.fromisoformat(data["received_at"])
    except ValueError as exc:
        raise FixtureError(f"{path.name}: received_at is not an ISO 8601 date-time") from exc
    if received_at.tzinfo is None:
        raise FixtureError(f"{path.name}: received_at must include a time zone")
    return Voicemail(
        voicemail_id=data["voicemail_id"],
        caller_number=data["caller_number"],
        caller_name=caller_name,
        received_at=received_at,
        transcript=data["transcript"],
    )


class VoicemailStore:
    """Read-only voicemails, newest first (ties broken by id)."""

    def __init__(self, voicemails: list[Voicemail] | tuple[Voicemail, ...]) -> None:
        by_id: dict[str, Voicemail] = {}
        for voicemail in voicemails:
            if voicemail.voicemail_id in by_id:
                raise FixtureError(f"duplicate voicemail_id {voicemail.voicemail_id!r}")
            by_id[voicemail.voicemail_id] = voicemail
        self._by_id = by_id
        ordered = sorted(voicemails, key=lambda v: v.voicemail_id)
        ordered.sort(key=lambda v: v.received_at, reverse=True)
        self._ordered = tuple(ordered)

    @classmethod
    def from_directory(cls, directory: Path = DEFAULT_FIXTURE_DIR) -> VoicemailStore:
        if not directory.is_dir():
            raise FixtureError(f"fixture directory not found: {directory}")
        return cls([_parse_fixture(path) for path in sorted(directory.glob("*.json"))])

    def __len__(self) -> int:
        return len(self._ordered)

    @property
    def newest_first(self) -> tuple[Voicemail, ...]:
        return self._ordered

    def get(self, voicemail_id: str) -> Voicemail | None:
        return self._by_id.get(voicemail_id)

    def find_by_hint(self, hint: str) -> tuple[Voicemail, ...]:
        return tuple(v for v in self._ordered if matches_hint(v, hint))
