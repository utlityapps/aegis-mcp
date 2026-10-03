"""Engine tests: verdicts, heuristics, the permission ledger, and the no-network guarantee."""

from __future__ import annotations

import ast
import json
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aegis.engine import (
    HEURISTICS,
    HEURISTICS_COUNT,
    VERDICT_SCAM_AT,
    VERDICT_SUSPICIOUS_AT,
    ActionLedger,
    ApprovalError,
    ApprovalExpiredError,
    analyze_voicemail,
    explain_red_flags,
    propose_actions,
    verdict_for,
)
from aegis.voicemails import VoicemailStore

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["aegis"]
FIXTURES = sorted((ROOT / "fixtures" / "voicemails").glob("*.json"))
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "asyncio", "requests", "httpx", "aiohttp"}

SAMPLES = {
    "government_impersonation": "This is the Internal Revenue Service.",
    "arrest_threat": "There is a warrant for your arrest.",
    "urgency_pressure": "You must act now.",
    "payment_gift_card": "Pay with Google Play gift cards.",
    "sensitive_info_request": "Please verify your Social Security number.",
    "secrecy_request": "Do not tell anyone about this call.",
    "family_emergency": "Grandma, I need bail money.",
    "account_suspended": "Your account has been suspended.",
    "press_button_callback": "Press 1 to speak with an agent.",
    "prize_offer": "Congratulations, you've won a cruise.",
    "remote_access": "Install an app so we can get remote access.",
    "suspicious_charge": "We saw a suspicious charge on your card.",
}


def test_heuristics_count_matches_product_agreement() -> None:
    assert HEURISTICS_COUNT == len(HEURISTICS) == PYPROJECT["engine"]["heuristics_count"] == 12
    assert len({h.flag_id for h in HEURISTICS}) == 12


def test_thresholds_match_product_agreement() -> None:
    assert VERDICT_SCAM_AT == PYPROJECT["engine"]["verdict_scam_at"]
    assert VERDICT_SUSPICIOUS_AT == PYPROJECT["engine"]["verdict_suspicious_at"]


@pytest.mark.parametrize(
    ("score", "verdict"),
    [(0, "LEGITIMATE"), (29, "LEGITIMATE"), (30, "SUSPICIOUS"), (59, "SUSPICIOUS"), (60, "SCAM"), (250, "SCAM")],
)
def test_verdict_thresholds(score: int, verdict: str) -> None:
    assert verdict_for(score) == verdict


@pytest.mark.parametrize("heuristic", HEURISTICS, ids=lambda h: h.flag_id)
def test_every_heuristic_fires_on_its_sample(heuristic) -> None:
    flags = {f.flag_id for f in analyze_voicemail(SAMPLES[heuristic.flag_id]).flags}
    assert heuristic.flag_id in flags


@pytest.mark.parametrize("heuristic", HEURISTICS, ids=lambda h: h.flag_id)
def test_every_heuristic_says_a_plain_sentence(heuristic) -> None:
    say = heuristic.say
    assert say.endswith(".") and len(say) <= 200 and len(say.split()) <= 20
    assert not any(token in say for token in ("_", "{", "}", "http", heuristic.flag_id))


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_fixture_verdicts(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert analyze_voicemail(data["transcript"]).verdict == data["expected_verdict"]


def test_fixture_mix_matches_product_agreement() -> None:
    verdicts = [json.loads(p.read_text(encoding="utf-8"))["expected_verdict"] for p in FIXTURES]
    assert len(verdicts) == PYPROJECT["mcp_server"]["fixtures"] == 8
    assert verdicts.count("SCAM") == 5 and verdicts.count("LEGITIMATE") == 3


def test_exactly_one_voicemail_matches_irs() -> None:
    store = VoicemailStore.from_directory()
    assert [v.voicemail_id for v in store.find_by_hint("IRS")] == ["vm-001"]


def test_analysis_is_deterministic_and_ordered() -> None:
    transcript = json.loads(FIXTURES[0].read_text(encoding="utf-8"))["transcript"]
    first, second = analyze_voicemail(transcript), analyze_voicemail(transcript)
    assert first == second
    keys = [(-f.weight, f.flag_id) for f in first.flags]
    assert keys == sorted(keys)
    assert first.risk_score == min(first.raw_score, 100)


def test_explain_windows_and_proposals() -> None:
    analysis = analyze_voicemail(json.loads(FIXTURES[0].read_text(encoding="utf-8"))["transcript"])
    assert explain_red_flags(analysis, 0, 2) == analysis.flags[:2]
    assert explain_red_flags(analysis, 2) == analysis.flags[2:]
    assert propose_actions(analysis) == ("block_number", "report_scam")
    assert propose_actions(analyze_voicemail("Hi Mom, dinner on Sunday?")) == ()


def test_engine_imports_no_network_modules() -> None:
    for source in (ROOT / "aegis").glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert name.split(".")[0] not in NETWORK_MODULES, f"{source.name} imports {name}"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def test_ledger_approve_is_single_use_and_records_receipt() -> None:
    ledger = ActionLedger()
    staged = ledger.stage_action("block_number", "vm-001", "+12025550147")
    assert staged.approval_token and staged.action
    receipt = ledger.approve_action(staged.approval_token, "block_number")
    assert receipt.simulated is True and receipt.caller_number == "+12025550147"
    with pytest.raises(ApprovalError):
        ledger.approve_action(staged.approval_token, "block_number")
    assert ledger.stage_action("block_number", "vm-001", "+12025550147").already_done == receipt


def test_ledger_wrong_kind_does_not_consume_token() -> None:
    ledger = ActionLedger()
    staged = ledger.stage_action("report_scam", "vm-001", "+12025550147")
    assert staged.approval_token
    with pytest.raises(ApprovalError):
        ledger.approve_action(staged.approval_token, "block_number")
    with pytest.raises(ApprovalError):
        ledger.approve_action(staged.approval_token, "report_scam", voicemail_id="vm-002")
    assert ledger.approve_action(staged.approval_token, "report_scam").kind == "report_scam"


def test_ledger_reject_drops_action() -> None:
    ledger = ActionLedger()
    staged = ledger.stage_action("block_number", "vm-001", "+12025550147")
    assert staged.approval_token
    ledger.reject_action(staged.approval_token, "block_number")
    with pytest.raises(ApprovalError):
        ledger.approve_action(staged.approval_token, "block_number")
    assert ledger.completed("block_number", "vm-001", "+12025550147") is None


def test_ledger_restage_rotates_token() -> None:
    ledger = ActionLedger()
    first = ledger.stage_action("block_number", "vm-001", "+12025550147")
    second = ledger.stage_action("block_number", "vm-001", "+12025550147")
    assert first.action and second.action and first.action.action_id == second.action.action_id
    assert first.approval_token and second.approval_token and first.approval_token != second.approval_token
    with pytest.raises(ApprovalError):
        ledger.approve_action(first.approval_token, "block_number")
    ledger.approve_action(second.approval_token, "block_number")


def test_ledger_expiry() -> None:
    clock = FakeClock()
    ledger = ActionLedger(ttl_seconds=60, clock=clock)
    staged = ledger.stage_action("block_number", "vm-001", "+12025550147")
    assert staged.approval_token
    clock.now += timedelta(seconds=61)
    with pytest.raises(ApprovalExpiredError):
        ledger.approve_action(staged.approval_token, "block_number")
    with pytest.raises(ApprovalError) as excinfo:
        ledger.approve_action(staged.approval_token, "block_number")
    assert not isinstance(excinfo.value, ApprovalExpiredError)


def test_ledger_rejects_out_of_range_ttl() -> None:
    with pytest.raises(ValueError):
        ActionLedger(ttl_seconds=59)
    with pytest.raises(ValueError):
        ActionLedger(ttl_seconds=3601)
    assert PYPROJECT["permission_layer"]["approval_ttl_seconds"] == 600
