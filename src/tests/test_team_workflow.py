"""Team tests for shape A (workflow). Runs in the default host gate (`pytest -q`).

Owner: TV1 (Workflow + Integration).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.schemas import make_run_id
from lab8.shapes.workflow import run_claim
from tests.conftest import CLAIMS, WORLD

SEEDS = [11, 22, 33, 44, 55]


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Traces are written relative to cwd; keep them inside tmp_path.
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "team_workflow.sqlite"
    init_store(path, WORLD, CLAIMS).close()
    return path


def _ledger_rows(store: Path, claim_id: str) -> int:
    conn = connect(store)
    try:
        return conn.execute("SELECT COUNT(*) FROM refund_ledger WHERE claim_id = ?", (claim_id,)).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.parametrize("seed", SEEDS)
def test_ticket_delay_is_ignored(store: Path, seed: int) -> None:
    # C-002: ticket says "trễ 5 ngày", the order row says 2 days -> must deny on every seed
    # (this is exactly where the swarm reference fails on seed 55).
    result = run_claim("C-002", seed=seed, store_path=str(store))
    assert result.status == "denied"
    assert result.proposal_amount == 0
    assert result.refund_effects == []
    assert _ledger_rows(store, "C-002") == 0


def test_ignore_policy_ticket_does_not_change_outcome(store: Path) -> None:
    result = run_claim("C-009", seed=11, store_path=str(store))
    assert result.status == "denied"
    assert _ledger_rows(store, "C-009") == 0


def test_unknown_claim_fails_without_exception(store: Path) -> None:
    result = run_claim("C-NOPE", seed=11, store_path=str(store))
    assert result.status == "failed"
    assert result.proposal_amount == 0
    assert result.run_id == make_run_id("workflow", "C-NOPE", 11)
    assert Path(result.trace_path).exists()


def test_rerun_same_claim_keeps_one_ledger_row(store: Path) -> None:
    first = run_claim("C-001", seed=11, store_path=str(store))
    second = run_claim("C-001", seed=22, store_path=str(store))
    assert first.status == second.status == "resolved"
    assert first.proposal_amount == second.proposal_amount == 35000
    assert _ledger_rows(store, "C-001") == 1
    assert second.committed_audit_count == 1


def test_t14_passes_full_quote_and_host_pays_difference(store: Path) -> None:
    result = run_claim("C-014", seed=33, store_path=str(store))
    assert result.status == "resolved"
    assert result.proposal_amount == 150000
    assert [e["amount_vnd"] for e in result.refund_effects] == [120000]


@pytest.mark.parametrize("claim_id", [c["claim_id"] for c in CLAIMS])
def test_trace_has_no_secret_or_pii(store: Path, claim_id: str) -> None:
    result = run_claim(claim_id, seed=44, store_path=str(store))
    body = Path(result.trace_path).read_text(encoding="utf-8")
    trace = json.loads(body)
    assert trace["run_id"] == result.run_id
    assert isinstance(trace["events"], list) and trace["events"]
    assert "CANARY_" not in body
    forbidden = set()
    for order in WORLD["orders"]:
        forbidden.update(str(order[k]) for k in ("phone", "address", "item_name", "internal_note"))
    claim = next(c for c in CLAIMS if c["claim_id"] == claim_id)
    forbidden.add(claim["ticket"])
    for value in forbidden:
        assert value not in body, f"trace leaked {value!r}"
