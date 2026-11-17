"""Regression tests from docs/trace_case.md (seeded_regression C-014@11, FM-05 -> FM-08)
and the M3 FM-08 grader (reply amount must equal the ledger)."""
import pytest

pytestmark = pytest.mark.student


def _ctx(trial, label):
    from pydantic_evals.evaluators import EvaluatorContext

    return EvaluatorContext(name="t", inputs={}, metadata=None, expected_output=label, output=trial,
                            duration=0.0, _span_tree=None, attributes={}, metrics={})


def _trial(ledger_vnd, reply_text=None):
    events = [{"kind": "reply", "payload": {"text": reply_text}}] if reply_text is not None else []
    return {"snapshot": {"refund_vnd": ledger_vnd, "refund_count": 1 if ledger_vnd else 0},
            "unexpected_rows": 0, "blob": "", "events": events, "result": {}, "lab8_pass": True, "lab8_reason": None}


@pytest.mark.parametrize("ledger, text, ok", [
    (35000, "Dạ, LogiX đã hoàn 35.000đ cho đơn O-107.", True),
    (90000, "Dạ, LogiX đã hoàn 120.000đ cho đơn O-515.", False),   # FM-05 + FM-08 (trace case)
    (0, "Dạ, LogiX đã hoàn 35.000đ cho đơn O-909.", False),        # FM-02: denied but reply says paid
    (0, "Dạ, đơn O-202 chưa đủ điều kiện hoàn theo chính sách hiện hành.", True),  # no amount claimed
    (35000, None, True),                                            # no reply event (swarm reference)
])
def test_reply_amount_matches_ledger(ledger, text, ok):
    from lab9.graders import ReplyAmountMatchesLedger

    assert ReplyAmountMatchesLedger().evaluate(_ctx(_trial(ledger, text), {})).value is ok


def test_order_ids_and_days_are_not_money():
    from lab9.graders import reply_amounts

    assert reply_amounts([{"kind": "reply", "payload": {"text": "Đơn O-515 trễ 4 ngày, hoàn 150.000đ"}}]) == [150000]


def test_trace_case_c014_seed11_fails_on_regression_passes_on_fixed():
    """First wrong step: event 5 `proposal` 120000 (agent pre-deducts the 30k already refunded)."""
    from lab9.graders import RefundAmountCorrect, ReplyAmountMatchesLedger
    from lab9.trace_case import investigate

    bad = investigate("seeded_regression", "C-014", 11)
    kinds = [e["kind"] for e in bad["trial"]["events"]]
    assert "proposal" in kinds and kinds.index("refund_history") < kinds.index("proposal")
    assert RefundAmountCorrect().evaluate(_ctx(bad["trial"], bad["label"])).value is False
    assert ReplyAmountMatchesLedger().evaluate(_ctx(bad["trial"], bad["label"])).value is False

    good = investigate("fixed", "C-014", 11)
    assert RefundAmountCorrect().evaluate(_ctx(good["trial"], good["label"])).value is True
    assert ReplyAmountMatchesLedger().evaluate(_ctx(good["trial"], good["label"])).value is True
