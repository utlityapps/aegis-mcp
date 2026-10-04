"""Aegis-defined JSON Schemas for the ecosystem pipeline.

These are **Aegis's own** event contracts. Ring, Bee and Fire TV publish no third-party
webhook or ambient-push schemas, so a real integration needs a bridge that maps vendor
events onto these shapes. Every schema is closed (`additionalProperties: false`).
"""

from __future__ import annotations

from typing import Any, Final

type JSONSchema = dict[str, Any]

RING_SCHEMA_ID: Final = "aegis.ring.event/v1"
WEARABLE_SCHEMA_ID: Final = "aegis.wearable.context/v1"
FIRETV_SCHEMA_ID: Final = "aegis.firetv.card/v1"

_EVENT_ID: Final[JSONSchema] = {"type": "string", "pattern": "^[A-Za-z0-9_-]{8,64}$"}
_DEVICE_ID: Final[JSONSchema] = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"}
_TIMESTAMP: Final[JSONSchema] = {"type": "string", "format": "date-time", "maxLength": 40}

RING_EVENT_SCHEMA: Final[JSONSchema] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": RING_SCHEMA_ID,
    "title": "Ring doorbell or motion event (Aegis bridge format)",
    "type": "object",
    "required": ["schema", "event_id", "event_type", "device", "occurred_at"],
    "properties": {
        "schema": {"const": RING_SCHEMA_ID},
        "event_id": _EVENT_ID,
        "event_type": {"enum": ["doorbell_press", "motion"]},
        "device": {
            "type": "object",
            "required": ["device_id", "location"],
            "properties": {
                "device_id": _DEVICE_ID,
                "location": {"enum": ["front_door", "back_door", "driveway", "garage", "other"]},
            },
            "additionalProperties": False,
        },
        "occurred_at": _TIMESTAMP,
        "person_detected": {"type": "boolean"},
    },
    "additionalProperties": False,
}

WEARABLE_CONTEXT_SCHEMA: Final[JSONSchema] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": WEARABLE_SCHEMA_ID,
    "title": "Wearable near-field transcript context (Aegis bridge format)",
    "type": "object",
    "required": ["schema", "event_id", "device_id", "captured_at", "consent", "speaker", "language", "transcript"],
    "properties": {
        "schema": {"const": WEARABLE_SCHEMA_ID},
        "event_id": _EVENT_ID,
        "device_id": _DEVICE_ID,
        "captured_at": _TIMESTAMP,
        "consent": {
            "type": "object",
            "description": "Recording other people can require everyone's consent. Events without it are refused.",
            "required": ["all_parties_consented", "basis"],
            "properties": {
                "all_parties_consented": {"const": True},
                "basis": {"enum": ["verbal", "written", "posted_notice"]},
            },
            "additionalProperties": False,
        },
        "speaker": {"enum": ["other", "wearer", "unknown"]},
        "language": {"const": "en"},
        "transcript": {"type": "string", "minLength": 1, "maxLength": 2000},
    },
    "additionalProperties": False,
}

FIRETV_CARD_SCHEMA: Final[JSONSchema] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": FIRETV_SCHEMA_ID,
    "title": "Fire TV ambient alert card",
    "type": "object",
    "required": [
        "schema", "card_id", "visit_id", "issued_at", "expires_at", "severity", "title", "body",
        "evidence", "threat", "buttons", "simulated",
    ],
    "properties": {
        "schema": {"const": FIRETV_SCHEMA_ID},
        "card_id": {"type": "string", "pattern": "^card-[0-9a-f]{16}$"},
        "visit_id": {"type": "string", "pattern": "^visit-[0-9a-f]{16}$"},
        "issued_at": _TIMESTAMP,
        "expires_at": _TIMESTAMP,
        "severity": {"enum": ["info", "caution", "alert"]},
        "title": {"type": "string", "minLength": 1, "maxLength": 60},
        "body": {"type": "string", "minLength": 1, "maxLength": 200},
        "evidence": {
            "type": "array",
            "maxItems": 3,
            "description": "Warning-sign sentences from the engine. Never raw transcript text.",
            "items": {"type": "string", "maxLength": 200},
        },
        "threat": {
            "oneOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "required": ["verdict", "risk_score"],
                    "properties": {
                        "verdict": {"enum": ["SCAM", "SUSPICIOUS", "LEGITIMATE"]},
                        "risk_score": {"type": "integer", "minimum": 0, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
            ]
        },
        "buttons": {
            "type": "array",
            "maxItems": 2,
            "description": "Display-only intents. No button blocks, reports or approves anything.",
            "items": {
                "type": "object",
                "required": ["id", "label"],
                "properties": {
                    "id": {"enum": ["ask_alexa", "dismiss"]},
                    "label": {"type": "string", "minLength": 1, "maxLength": 24},
                },
                "additionalProperties": False,
            },
        },
        "simulated": {"const": True},
    },
    "additionalProperties": False,
}
