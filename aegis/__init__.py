"""Aegis: deterministic voicemail scam analysis for older adults."""

from aegis.engine import (
    ActionLedger,
    Analysis,
    ApprovalError,
    ApprovalExpiredError,
    RedFlag,
    Receipt,
    analyze_voicemail,
    explain_red_flags,
    propose_actions,
)
from aegis.voicemails import Voicemail, VoicemailStore

__all__ = [
    "ActionLedger",
    "Analysis",
    "ApprovalError",
    "ApprovalExpiredError",
    "RedFlag",
    "Receipt",
    "Voicemail",
    "VoicemailStore",
    "analyze_voicemail",
    "explain_red_flags",
    "propose_actions",
]
