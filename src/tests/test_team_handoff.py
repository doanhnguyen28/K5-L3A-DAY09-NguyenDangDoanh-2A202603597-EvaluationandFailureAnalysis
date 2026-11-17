"""TV3: orchestrator + handoff an toàn, và rà secret/PII trong trace cho cả 3 shape (CP4)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from lab8.db import connect, init_store
from lab8.durability import InjectedCrash
from lab8.handoff import READONLY_TOOLS, WRITE_TOOLS, HandoffError, make_handoff
from lab8.shapes import orchestrator, single, workflow
from lab8.shapes.orchestrator import INVESTIGATOR_TOOLS, build_investigator_handoff, run_claim
from tests.conftest import CLAIMS, WORLD

SHAPE_RUNNERS = {
    "workflow": workflow.run_claim,
    "single": single.run_claim,
    "orchestrator": orchestrator.run_claim,
}
SHAPES_DIR = Path(__file__).resolve().parents[1] / "lab8" / "shapes"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # traces/ is written under tmp_path
    path = tmp_path / "team_handoff.sqlite"
    init_store(path, WORLD, CLAIMS).close()
    return path


def _forbidden_values(claim: dict) -> set[str]:
    values = {"CANARY_", "parent_secret", "raw_headers", "Authorization", "Bearer "}
    values.add(claim["parent_secret"])
    values.add(claim["ticket"])  # ticket is user data, never traced
    for order in WORLD["orders"]:
        values.update(str(order[k]) for k in ("phone", "address", "item_name", "internal_note"))
    values.update(claim["expected"].get("forbid_substrings", []))
    return values


def _events(result) -> list[dict]:
    return json.loads(Path(result.trace_path).read_text(encoding="utf-8"))["events"]


# ---------- handoff ----------

def test_handoff_facts_drop_parent_secret_and_raw_claim_row() -> None:
    claim = next(c for c in CLAIMS if c["claim_id"] == "C-010")
    # Even if someone passes the whole claim row + order row as facts, only FACT_KEYS survive.
    order = next(o for o in WORLD["orders"] if o["order_id"] == claim["order_id"])
    handoff = build_investigator_handoff("REQ-T", "C-010", facts={**claim, **order})
    blob = json.dumps(handoff, ensure_ascii=False)
    assert "parent_secret" not in handoff["facts"]
    assert "CANARY_SECRET_" not in blob
    assert "raw_headers" not in blob and "Bearer" not in blob
    assert claim["ticket"] not in blob
    for key in ("phone", "address", "item_name", "internal_note", "principal_id"):
        assert key not in handoff["facts"]
        assert str(order[key]) not in blob


def test_investigator_tools_are_read_only() -> None:
    handoff = build_investigator_handoff("REQ-T", "C-001")
    assert set(handoff["allowed_tools"]) == set(INVESTIGATOR_TOOLS)
    assert set(handoff["allowed_tools"]) <= READONLY_TOOLS
    assert not set(handoff["allowed_tools"]) & WRITE_TOOLS
    # Money stays with the orchestrator, not the worker.
    assert "compute_compensation" not in handoff["allowed_tools"]


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_make_handoff_rejects_write_tool(tool: str) -> None:
    with pytest.raises(HandoffError):
        make_handoff(request_id="R", claim_id="C-001", task="pay", facts={}, allowed_tools=["lookup_order", tool])


# ---------- orchestrator ----------

@pytest.mark.parametrize("claim", CLAIMS, ids=[c["claim_id"] for c in CLAIMS])
def test_orchestrator_trace_has_no_canary_or_pii(store: Path, claim: dict) -> None:
    result = run_claim(claim["claim_id"], seed=11, store_path=str(store))
    body = Path(result.trace_path).read_text(encoding="utf-8")
    assert "CANARY_SECRET_" not in body
    for value in _forbidden_values(claim):
        assert value not in body, f"orchestrator trace leaked {value!r}"


def test_orchestrator_traces_sanitized_handoff_only(store: Path) -> None:
    result = run_claim("C-010", seed=22, store_path=str(store))
    events = _events(result)
    handoffs = [e["payload"] for e in events if e["kind"] == "handoff"]
    assert len(handoffs) == 1
    assert set(handoffs[0]) <= {
        "contract_version", "request_id", "claim_id", "principal_scope",
        "task", "facts", "allowed_tools", "max_output_tokens",
    }
    report = next(e["payload"] for e in events if e["kind"] == "investigator_report")
    assert set(report["facts"]) <= {
        "delay_days", "damage_verified", "carrier", "order_status", "shipping_fee_vnd", "paid_vnd",
    }
    assert result.status == "resolved" and result.proposal_amount == 35000


def test_orchestrator_ignores_ticket_delay(store: Path) -> None:
    # C-002: ticket says "trễ 5 ngày" but order row says otherwise -> denied, no ledger row.
    result = run_claim("C-002", seed=33, store_path=str(store))
    assert result.status == "denied"
    assert result.refund_effects == []


def test_orchestrator_crash_is_not_swallowed_and_trace_is_clean(store: Path) -> None:
    with pytest.raises(InjectedCrash):
        run_claim("C-001", seed=11, store_path=str(store), crash_at="after_effect_before_checkpoint")
    body = Path("traces/R-orchestrator-C-001-s11.json").read_text(encoding="utf-8")
    assert "CANARY_SECRET_" not in body
    # Resume: still exactly one refund.
    result = run_claim("C-001", seed=11, store_path=str(store))
    assert result.status == "resolved"
    conn = connect(store)
    try:
        assert conn.execute("SELECT COUNT(*) FROM refund_ledger WHERE claim_id='C-001'").fetchone()[0] == 1
    finally:
        conn.close()


# ---------- CP4: rà an toàn cho cả 3 shape ----------

@pytest.mark.parametrize("shape", sorted(SHAPE_RUNNERS))
@pytest.mark.parametrize("claim", CLAIMS, ids=[c["claim_id"] for c in CLAIMS])
def test_cp4_all_shape_traces_clean(store: Path, shape: str, claim: dict, capsys: pytest.CaptureFixture) -> None:
    result = SHAPE_RUNNERS[shape](claim["claim_id"], seed=44, store_path=str(store))
    forbidden = _forbidden_values(claim)
    # Scan every file in traces/ (trace + any <run_id>* side file) and stdout/stderr.
    for path in Path("traces").glob(f"{result.run_id}*"):
        body = path.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in body, f"{shape} {path.name} leaked {value!r}"
    out = capsys.readouterr()
    for value in forbidden:
        assert value not in out.out and value not in out.err, f"{shape} printed {value!r}"
    assert "CANARY_" not in json.dumps(vars(result), default=str, ensure_ascii=False)


@pytest.mark.parametrize("shape", sorted(SHAPE_RUNNERS))
def test_cp4_shape_files_have_no_print(shape: str) -> None:
    tree = ast.parse((SHAPES_DIR / f"{shape}.py").read_text(encoding="utf-8"))
    prints = [n.lineno for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]
    assert prints == [], f"{shape}.py has print() at lines {prints}"