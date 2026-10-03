"""Tool definitions exactly as specified in docs/architecture.md §6.

Shared definitions are written out inline in every schema (no `$ref`), because
some MCP clients don't resolve references.
"""

from __future__ import annotations

import copy
from typing import Any, Final, Literal

type JSONSchema = dict[str, Any]
type ToolName = Literal["list_voicemails", "check_voicemail", "explain_red_flags", "block_number", "report_scam"]

TOOL_NAMES: Final[tuple[ToolName, ...]] = (
    "list_voicemails",
    "check_voicemail",
    "explain_red_flags",
    "block_number",
    "report_scam",
)

VOICEMAIL_ID_PATTERN: Final = "^[a-z0-9][a-z0-9_-]{0,63}$"
APPROVAL_TOKEN_PATTERN: Final = "^[A-Za-z0-9_-]{32,128}$"

_VOICEMAIL_ID: Final[JSONSchema] = {
    "type": "string",
    "pattern": VOICEMAIL_ID_PATTERN,
    "description": "Opaque voicemail identifier returned by list_voicemails or check_voicemail. Never read aloud.",
}
_VERDICT: Final[JSONSchema] = {"type": "string", "enum": ["SCAM", "SUSPICIOUS", "LEGITIMATE"]}
_RISK_SCORE: Final[JSONSchema] = {"type": "integer", "minimum": 0, "maximum": 100}
_APPROVAL_TOKEN: Final[JSONSchema] = {
    "type": "string",
    "pattern": APPROVAL_TOKEN_PATTERN,
    "description": "Single-use token from a staged action. Pass back only after the person says yes (or no). Never read aloud.",
}


def _voicemail_id() -> JSONSchema:
    return copy.deepcopy(_VOICEMAIL_ID)


def _verdict() -> JSONSchema:
    return copy.deepcopy(_VERDICT)


def _risk_score() -> JSONSchema:
    return copy.deepcopy(_RISK_SCORE)


def _approval_token() -> JSONSchema:
    return copy.deepcopy(_APPROVAL_TOKEN)


def _voicemail_summary() -> JSONSchema:
    return {
        "type": "object",
        "required": ["voicemail_id", "caller_label", "caller_number", "received_at"],
        "properties": {
            "voicemail_id": _voicemail_id(),
            "caller_label": {
                "type": "string",
                "maxLength": 80,
                "description": "Spoken form: caller name if known, else the number.",
            },
            "caller_number": {
                "type": "string",
                "maxLength": 32,
                "description": "Caller number as given in fixture metadata (trusted input).",
            },
            "received_at": {"type": "string", "format": "date-time"},
        },
        "additionalProperties": False,
    }


def _annotations(*, read_only: bool) -> dict[str, bool]:
    return {
        "readOnlyHint": read_only,
        "destructiveHint": not read_only,
        "idempotentHint": read_only,
        "openWorldHint": False,
    }


LIST_VOICEMAILS: Final[JSONSchema] = {
    "name": "list_voicemails",
    "title": "List voicemails",
    "description": (
        "Lists the person's voicemails, newest first, up to 5 at a time. Call this when the person asks what "
        "voicemails they have, or when you need to find a voicemail before checking it. Returns caller names or "
        "numbers and when each message arrived. Does not judge whether any message is a scam; use check_voicemail "
        "for that."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "default": 5,
                "description": "How many voicemails to return (1-5).",
            },
            "cursor": {
                "type": "string",
                "maxLength": 64,
                "description": (
                    'Pass next_cursor from a previous call to hear more ("tell me more", "next"). '
                    "Omit for the newest voicemails."
                ),
            },
        },
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "required": ["voicemails", "total", "next_cursor", "say"],
        "properties": {
            "voicemails": {"type": "array", "maxItems": 5, "items": _voicemail_summary()},
            "total": {"type": "integer", "minimum": 0},
            "next_cursor": {"type": ["string", "null"]},
            "say": {"type": "string", "maxLength": 400},
        },
        "additionalProperties": False,
    },
    "annotations": _annotations(read_only=True),
}

CHECK_VOICEMAIL: Final[JSONSchema] = {
    "name": "check_voicemail",
    "title": "Check a voicemail for scams",
    "description": (
        "Checks one voicemail for signs of a scam and returns a verdict: SCAM, SUSPICIOUS, or LEGITIMATE, with the "
        "top warning signs in plain words. Call this when the person asks to check a voicemail, for example 'the "
        "voicemail from the IRS' (use caller_hint) or 'my last voicemail' (pass no arguments). Verdicts come from "
        "fixed rules, not opinion. If more than one voicemail matches, returns up to 5 candidates to ask the person "
        "about. Never blocks or reports anything."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "voicemail_id": _voicemail_id(),
            "caller_hint": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": (
                    "Who the person says called, in their words: a name, organization, or part of a number. "
                    "Examples: 'IRS', 'Internal Revenue Service', 'the bank', 'Medicare', 'my grandson', '555-0147'."
                ),
            },
        },
        "not": {"required": ["voicemail_id", "caller_hint"]},
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "required": ["status", "say"],
        "properties": {
            "status": {"type": "string", "enum": ["checked", "ambiguous"]},
            "voicemail": _voicemail_summary(),
            "verdict": _verdict(),
            "risk_score": _risk_score(),
            "top_flags": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "required": ["flag_id", "say"],
                    "properties": {
                        "flag_id": {"type": "string"},
                        "say": {"type": "string", "maxLength": 200},
                    },
                    "additionalProperties": False,
                },
            },
            "flag_count": {"type": "integer", "minimum": 0, "maximum": 12},
            "suggested_actions": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "enum": ["block_number", "report_scam"]},
                "description": "Actions Aegis suggests offering. Nothing is staged yet.",
            },
            "candidates": {"type": "array", "minItems": 2, "maxItems": 5, "items": _voicemail_summary()},
            "say": {"type": "string", "maxLength": 400},
        },
        "allOf": [
            {
                "if": {"properties": {"status": {"const": "checked"}}},
                "then": {
                    "required": ["voicemail", "verdict", "risk_score", "top_flags", "flag_count", "suggested_actions"]
                },
            },
            {
                "if": {"properties": {"status": {"const": "ambiguous"}}},
                "then": {"required": ["candidates"]},
            },
        ],
        "additionalProperties": False,
    },
    "annotations": _annotations(read_only=True),
}

EXPLAIN_RED_FLAGS: Final[JSONSchema] = {
    "name": "explain_red_flags",
    "title": "Explain warning signs",
    "description": (
        "Explains, in plain spoken sentences, each warning sign Aegis found in a voicemail. Call this after "
        "check_voicemail when the person asks why, or says 'tell me more'. Returns up to 5 warning signs per call; "
        "use start to continue. For a safe-looking voicemail, says that no warning signs were found."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["voicemail_id"],
        "properties": {
            "voicemail_id": _voicemail_id(),
            "start": {
                "type": "integer",
                "minimum": 0,
                "maximum": 11,
                "default": 0,
                "description": (
                    "Index of the first warning sign to explain. Use next_start from the previous call for "
                    "'tell me more'."
                ),
            },
            "max_flags": {
                "type": "integer",
                "minimum": 1,
                "maximum": 5,
                "default": 3,
                "description": "How many warning signs to explain in this turn (1-5).",
            },
        },
        "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "required": ["voicemail_id", "verdict", "risk_score", "flags", "total_flags", "next_start", "say"],
        "properties": {
            "voicemail_id": _voicemail_id(),
            "verdict": _verdict(),
            "risk_score": _risk_score(),
            "flags": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "required": ["flag_id", "weight", "say"],
                    "properties": {
                        "flag_id": {"type": "string", "description": "Stable heuristic identifier. Not for speech."},
                        "weight": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Fixed heuristic weight. Not for speech.",
                        },
                        "say": {
                            "type": "string",
                            "maxLength": 200,
                            "description": "Senior-friendly sentence from the engine.",
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "total_flags": {"type": "integer", "minimum": 0, "maximum": 12},
            "next_start": {"type": ["integer", "null"], "minimum": 1, "maximum": 11},
            "say": {"type": "string", "maxLength": 600},
        },
        "additionalProperties": False,
    },
    "annotations": _annotations(read_only=True),
}


def _action_tool(kind: Literal["block_number", "report_scam"], title: str, description: str) -> JSONSchema:
    is_report = kind == "report_scam"
    action_required = ["action_id", "voicemail_id", "caller_label", "caller_number", "created_at", "expires_at"]
    action_properties: JSONSchema = {
        "action_id": {"type": "string"},
        "voicemail_id": _voicemail_id(),
        "caller_label": {"type": "string"},
        "caller_number": {"type": "string"},
        "created_at": {"type": "string", "format": "date-time"},
        "expires_at": {"type": "string", "format": "date-time"},
    }
    receipt_required = ["receipt_id", "kind", "caller_number", "executed_at", "simulated"]
    receipt_properties: JSONSchema = {
        "receipt_id": {"type": "string"},
        "kind": {"type": "string", "const": kind},
        "caller_number": {"type": "string"},
        "executed_at": {"type": "string", "format": "date-time"},
        "simulated": {"type": "boolean", "const": True},
    }
    if is_report:
        action_required.append("verdict")
        action_properties["verdict"] = _verdict()
        receipt_required.append("voicemail_id")
        receipt_properties["voicemail_id"] = _voicemail_id()

    return {
        "name": kind,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "voicemail_id": _voicemail_id(),
                "approval_token": _approval_token(),
                "decision": {
                    "type": "string",
                    "enum": ["approve", "reject"],
                    "description": (
                        "'approve' only after the person says yes (yes, sure, go ahead, do it). "
                        "'reject' if they say no (no, cancel, don't, never mind)."
                    ),
                },
            },
            "dependentRequired": {"approval_token": ["decision"], "decision": ["approval_token"]},
            "anyOf": [{"required": ["voicemail_id"]}, {"required": ["approval_token"]}],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "required": ["status", "kind", "say"],
            "properties": {
                "status": {"type": "string", "enum": ["staged", "executed", "rejected", "already_done"]},
                "kind": {"type": "string", "const": kind},
                "action": {
                    "type": "object",
                    "required": action_required,
                    "properties": action_properties,
                    "additionalProperties": False,
                },
                "approval_token": _approval_token(),
                "receipt": {
                    "type": "object",
                    "required": receipt_required,
                    "properties": receipt_properties,
                    "additionalProperties": False,
                },
                "say": {"type": "string", "maxLength": 400},
            },
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": "staged"}}},
                    "then": {"required": ["action", "approval_token"]},
                },
                {
                    "if": {"properties": {"status": {"const": "executed"}}},
                    "then": {"required": ["receipt"], "not": {"required": ["approval_token"]}},
                },
                {
                    "if": {"properties": {"status": {"enum": ["rejected", "already_done"]}}},
                    "then": {"not": {"required": ["approval_token"]}},
                },
            ],
            "additionalProperties": False,
        },
        "annotations": _annotations(read_only=False),
    }


BLOCK_NUMBER: Final[JSONSchema] = _action_tool(
    "block_number",
    "Block a caller",
    "Blocks the phone number that left a voicemail. Two steps, always: (1) call with voicemail_id to prepare the "
    "block; read the returned 'say' to the person and ask them to confirm. (2) Only after the person clearly says "
    "yes, call again with approval_token and decision 'approve'. If they say no, call with decision 'reject'. Never "
    "approve without an explicit yes in this conversation. Never read the token aloud. This only blocks; to report a "
    "scam use report_scam.",
)

REPORT_SCAM: Final[JSONSchema] = _action_tool(
    "report_scam",
    "Report a scam call",
    "Reports a voicemail as a scam call. Two steps, always: (1) call with voicemail_id to prepare the report; read "
    "the returned 'say' and ask the person to confirm. (2) Only after a clear yes, call again with approval_token and "
    "decision 'approve'; on no, decision 'reject'. Never read the token aloud. This only reports; to block the caller "
    "use block_number.",
)

TOOL_DEFINITIONS: Final[dict[ToolName, JSONSchema]] = {
    "list_voicemails": LIST_VOICEMAILS,
    "check_voicemail": CHECK_VOICEMAIL,
    "explain_red_flags": EXPLAIN_RED_FLAGS,
    "block_number": BLOCK_NUMBER,
    "report_scam": REPORT_SCAM,
}

ERROR_BODY_SCHEMA: Final[JSONSchema] = {
    "type": "object",
    "required": ["code", "say"],
    "properties": {
        "code": {
            "type": "string",
            "enum": ["invalid_input", "not_found", "permission_denied", "token_expired", "unavailable"],
        },
        "field": {"type": "string", "description": "Offending argument, for the model. Not for speech."},
        "say": {"type": "string", "maxLength": 300, "description": "Plain sentence plus one next step."},
    },
    "additionalProperties": False,
}
