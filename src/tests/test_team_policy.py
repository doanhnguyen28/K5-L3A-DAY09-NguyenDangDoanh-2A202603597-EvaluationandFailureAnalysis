"""TV4 regression tests for approval, order caps, retries, and crash resume."""

from __future__ import annotations

import pytest

from lab8.db import connect, init_store
from lab8.durability import InjectedCrash, committed_audit_count, get_refund, issue_refund
from lab8.shapes import orchestrator, single, workflow
from tests.conftest import CLAIMS, WORLD

SHAPES = {
    "workflow": workflow.run_claim,
    "single": single.run_claim,
    "orchestrator": orchestrator.run_claim,
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "team_policy.sqlite"
    init_store(path, WORLD, CLAIMS).close()
    return path


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("expired", "approval_expired"),
        ("revoked", "approval_revoked"),
        ("wrong_claim", "approval_claim_mismatch"),
    ],
)
def test_shape_does_not_refund_without_its_valid_approval(store, shape, mutation, expected_reason):
    conn = connect(store)
    if mutation == "expired":
        conn.execute("UPDATE approvals SET expires_at = '2020-01-01' WHERE approval_id = 'A-001'")
    elif mutation == "revoked":
        conn.execute("UPDATE approvals SET status = 'revoked' WHERE approval_id = 'A-001'")
    else:
        # A-002 belongs to C-002, so it cannot authorize C-001.
        conn.execute("UPDATE claims SET approval_id = 'A-002' WHERE claim_id = 'C-001'")
    conn.commit()
    conn.close()

    result = SHAPES[shape]("C-001", seed=11, store_path=str(store))
    conn = connect(store)
    try:
        assert result.status == "denied"
        assert result.proposal_amount == 35000
        assert result.error_reason == expected_reason
        assert get_refund(conn, "C-001") is None
        assert committed_audit_count(conn, "C-001") == 0
    finally:
        conn.close()


@pytest.mark.parametrize("shape", SHAPES)
def test_shape_does_not_refund_an_order_that_was_already_paid(store, shape):
    conn = connect(store)
    assert issue_refund(conn, "C-001", 35000)["ok"]
    conn.close()

    result = SHAPES[shape]("C-010", seed=11, store_path=str(store))
    conn = connect(store)
    try:
        assert result.status == "denied"
        assert result.proposal_amount == 35000
        assert result.error_reason == "order_already_refunded"
        assert get_refund(conn, "C-010") is None
    finally:
        conn.close()


@pytest.mark.parametrize("shape", SHAPES)
def test_shape_retry_keeps_one_ledger_row_and_one_committed_audit(store, shape):
    first = SHAPES[shape]("C-011", seed=11, store_path=str(store))
    second = SHAPES[shape]("C-011", seed=22, store_path=str(store))
    conn = connect(store)
    try:
        assert first.status == second.status == "resolved"
        assert get_refund(conn, "C-011") is not None
        assert conn.execute("SELECT COUNT(*) FROM refund_ledger WHERE claim_id='C-011'").fetchone()[0] == 1
        assert committed_audit_count(conn, "C-011") == 1
    finally:
        conn.close()


@pytest.mark.parametrize("shape", SHAPES)
def test_shape_passes_full_t14_quote_and_host_pays_only_difference(store, shape):
    result = SHAPES[shape]("C-014", seed=33, store_path=str(store))
    conn = connect(store)
    try:
        effect = get_refund(conn, "C-014")
        assert result.status == "resolved"
        assert result.proposal_amount == 150000
        assert effect is not None and int(effect["amount_vnd"]) == 120000
    finally:
        conn.close()


@pytest.mark.parametrize("shape", SHAPES)
def test_shape_crash_resume_does_not_duplicate_refund(store, shape):
    with pytest.raises(InjectedCrash):
        SHAPES[shape](
            "C-012", seed=11, store_path=str(store), crash_at="after_effect_before_checkpoint"
        )

    resumed = SHAPES[shape]("C-012", seed=11, store_path=str(store))
    conn = connect(store)
    try:
        assert resumed.status == "resolved"
        assert conn.execute("SELECT COUNT(*) FROM refund_ledger WHERE claim_id='C-012'").fetchone()[0] == 1
        assert committed_audit_count(conn, "C-012") == 1
    finally:
        conn.close()
