"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import stats
from lab9.eval import SPEC, load_spec, per_item


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Must catch at least:
      - any metric value in pr["metrics"] that is NaN or inf
      - len(pr["rows"]) != manifest items x len(manifest seeds)   (dropped / duplicated rows)
      - the (item_id, seed) set of PR differs from the baseline's (cherry-picked seeds/items)
      - pr["evaluator_failures"] non-empty, or a row whose value for a graded spec metric is
        None (a crashed grader silently scores as "missing" and drops out of the mean)
      - base_spec given and a threshold in spec is LOOSER than in base_spec for the same
        eval-set version (ratchet: max may not rise, min may not fall, a blocking metric may
        not become non-blocking, a metric may not disappear; the same for spec["slices"],
        including dropping no_significant_drop)
      - an item's slice tags in PR rows differ from the baseline's (relabelling an item out of a
        slice hides its regression from the slice gate)"""
    problems: list[str] = []
    graded = _graded_ids(spec, pr, base)
    # 1. Both reports must be internally consistent: rows <-> manifest <-> metrics.
    for who, rep in (("PR", pr), ("baseline", base)):
        problems += [f"{who}: {p}" for p in _report_problems(rep, spec, graded)]
    # 2. Same evidence on both sides: same seeds, same items, same (item, seed) pairs.
    pm, bm = pr.get("manifest", {}), base.get("manifest", {})
    if list(pm.get("seeds") or []) != list(bm.get("seeds") or []):
        problems.append(f"seeds {pm.get('seeds')} differ from the baseline's {bm.get('seeds')} (cherry-picked seeds?)")
    if pm.get("items") != bm.get("items"):
        problems.append(f"manifest items {pm.get('items')} differ from the baseline's {bm.get('items')}")
    pk, bk = _pairs(pr), _pairs(base)
    if set(pk) != set(bk):
        missing, extra = sorted(set(bk) - set(pk)), sorted(set(pk) - set(bk))
        problems.append(f"(item, seed) set differs from the baseline: {len(missing)} missing "
                        f"(first {missing[:3]}), {len(extra)} extra (first {extra[:3]})")
    # 3. Slice tags come from the label, never from the PR: they must match the baseline per item.
    ps, bs = _slices_by_item(pr), _slices_by_item(base)
    relabelled = sorted(i for i in set(ps) & set(bs) if ps[i] != bs[i])
    if relabelled:
        i = relabelled[0]
        problems.append(f"slice tags changed on {len(relabelled)} item(s), e.g. {i}: {sorted(bs[i])} -> {sorted(ps[i])}")
    # 4. Ratchet: on the same eval-set version thresholds may only get stricter.
    if base_spec is not None:
        problems += _ratchet_problems(spec, base_spec)
    return problems


# ---- M7 helpers -------------------------------------------------------------------------------

SAFETY_COUNTS = {"secret_leak", "approval_bypass"}   # aggregated as counts by lab9.eval.aggregate


def _graded_ids(spec: dict, *reps: dict) -> list[str]:
    """Spec metrics that are scored per row (a grader writes a value into every row)."""
    row_keys: set[str] = set()
    for rep in reps:
        for r in rep.get("rows") or []:
            row_keys |= set(r)
    ids = [m["id"] for m in spec.get("metrics") or []]
    try:
        from lab9.graders import GRADERS
        ids_graded = [i for i in ids if i in GRADERS or i in row_keys]
    except Exception:   # pragma: no cover - graders always importable in the lab
        ids_graded = [i for i in ids if i in row_keys]
    return ids_graded


def _pairs(rep: dict) -> list[tuple[str, int]]:
    return [(r.get("item_id"), r.get("seed")) for r in rep.get("rows") or []]


def _slices_by_item(rep: dict) -> dict[str, frozenset]:
    """item -> the set of tag-sets seen on its rows (one tag-set when the report is consistent).
    Relabelling only the failing seed of an item still shows up as a second tag-set."""
    out: dict[str, set] = {}
    for r in rep.get("rows") or []:
        out.setdefault(r["item_id"], set()).add(frozenset(r.get("slices") or []))
    return {k: frozenset(v) for k, v in out.items()}


def _bad_number(v) -> bool:
    return isinstance(v, float) and not math.isfinite(v)


def _report_problems(rep: dict, spec: dict, graded: list[str]) -> list[str]:
    problems: list[str] = []
    rows = rep.get("rows") or []
    man = rep.get("manifest") or {}
    # NaN / inf anywhere in the headline metrics.
    for k, v in (rep.get("metrics") or {}).items():
        if _bad_number(v):
            problems.append(f"metric {k} is {v} (NaN/inf drops out of every comparison)")
    # Row count and uniqueness: dropped rows, or duplicated passing rows padding the count back up.
    try:
        want = int(man.get("items")) * len(man.get("seeds") or [])
    except (TypeError, ValueError):
        want = None
    if want is None or len(rows) != want:
        problems.append(f"{len(rows)} rows, manifest says {man.get('items')} items x {len(man.get('seeds') or [])} seeds = {want}")
    pairs = _pairs(rep)
    dupes = sorted({p for p in pairs if pairs.count(p) > 1})
    if dupes:
        problems.append(f"{len(dupes)} duplicated (item, seed) row(s), e.g. {dupes[0]}")
    seeds = set(man.get("seeds") or [])
    off = sorted({s for _, s in pairs if s not in seeds}, key=str)
    if off:
        problems.append(f"rows with seeds {off} not in the manifest {sorted(seeds)}")
    if len({i for i, _ in pairs}) != man.get("items"):
        problems.append(f"{len({i for i, _ in pairs})} distinct items in rows, manifest says {man.get('items')}")
    # A crashed grader must not vanish from the mean.
    if rep.get("evaluator_failures"):
        problems.append(f"{len(rep['evaluator_failures'])} evaluator failure(s), first: {rep['evaluator_failures'][0]}")
    for m in graded:
        holes = [r for r in rows if m in r and not isinstance(r[m], bool)]
        absent = [r for r in rows if m not in r] if any(m in r for r in rows) else []
        if holes or absent:
            r = (holes or absent)[0]
            problems.append(f"{len(holes) + len(absent)} row(s) with no valid {m} value "
                            f"(e.g. {r.get('item_id')}@{r.get('seed')} = {r.get(m)!r}): a crashed grader")
    # Headline metrics must be what the rows say (no hand-edited numbers).
    problems += _recompute_problems(rep, spec)
    return problems


def _recompute_problems(rep: dict, spec: dict) -> list[str]:
    rows = rep.get("rows") or []
    metrics = rep.get("metrics") or {}
    if not rows:
        return ["report has no rows"]
    safety = SAFETY_COUNTS | {m["id"] for m in spec.get("metrics") or [] if m.get("kind") == "safety"}
    want: dict[str, float] = {}
    for k in metrics:
        if all(k in r for r in rows):
            vals = [r[k] for r in rows if isinstance(r[k], bool)]
            if k in safety:
                want[k] = float(sum(vals))
            elif vals:
                want[k] = round(sum(vals) / len(vals), 4)
    by_item: dict[str, list[bool]] = {}
    for r in rows:
        by_item.setdefault(r["item_id"], []).append(bool(r.get("pass_rate")))
    if "pass_rate" in rows[0]:
        if "pass_5" in metrics:
            want["pass_5"] = round(sum(all(v) for v in by_item.values()) / len(by_item), 4)
        for k in spec.get("pass_k") or []:
            for name, fn in (("pass_at", stats.pass_at_k), ("pass_hat", stats.pass_hat_k)):
                key = f"{name}_{k}"
                if key not in metrics:
                    continue
                if min(len(v) for v in by_item.values()) < k:
                    want[key] = None
                    continue
                try:
                    est = [fn(len(v), sum(v), k) for v in by_item.values()]
                except (NotImplementedError, ValueError):
                    continue
                want[key] = round(sum(est) / len(est), 4)
    out = []
    for k, w in want.items():
        got = metrics.get(k)
        if w is None or got is None:
            if w != got:
                out.append(f"metric {k} = {got!r} but the rows give {w!r}")
            continue
        if not isinstance(got, (int, float)) or _bad_number(got) or abs(float(got) - w) > 1e-3:
            out.append(f"metric {k} = {got!r} but the rows give {w}")
    return out


def _ratchet_problems(spec: dict, base_spec: dict) -> list[str]:
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        return []   # a new eval-set version starts a new ratchet (with a new baseline)
    problems: list[str] = []

    def compare(label: str, new: dict | None, old: dict) -> None:
        if new is None:
            problems.append(f"RATCHET {label} removed")
            return
        if old.get("blocking") and not new.get("blocking"):
            problems.append(f"RATCHET {label} no longer blocking")
        nt = new.get("threshold", new) if "threshold" in old or "threshold" in new else new
        ot = old.get("threshold", old) if "threshold" in old or "threshold" in new else old
        for key in ("min",):
            if key in ot and (key not in nt or nt[key] < ot[key]):
                problems.append(f"RATCHET {label} {key} {ot[key]} -> {nt.get(key)} (looser)")
        for key in ("max", "max_increase"):
            if key in ot and (key not in nt or nt[key] > ot[key]):
                problems.append(f"RATCHET {label} {key} {ot[key]} -> {nt.get(key)} (looser)")
        if ot.get("no_significant_drop") and not nt.get("no_significant_drop"):
            problems.append(f"RATCHET {label} dropped no_significant_drop")

    new_metrics = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        compare(f"metric {m['id']}", new_metrics.get(m["id"]), m)
    new_slices = {(s["name"], s["metric"]): s for s in spec.get("slices") or []}
    for s in base_spec.get("slices") or []:
        compare(f"slice {s['name']}/{s['metric']}", new_slices.get((s["name"], s["metric"])), s)
    for key in ("seeds", "eval_set", "labels"):
        if key in base_spec and spec.get(key) != base_spec.get(key):
            problems.append(f"RATCHET {key} changed {base_spec.get(key)} -> {spec.get(key)} on the same eval-set version")
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked: list[str] = []
    lines: list[str] = []
    bs = spec.get("bootstrap", {})
    for s in spec.get("slices") or []:
        name, metric = s["name"], s["metric"]
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note = "ok", ""
        val = round(sum(a.values()) / len(a), 4) if a else None
        if val is None:
            verdict = "missing"          # an empty slice proves nothing: never a silent pass
        elif "min" in s and val < s["min"]:
            verdict = "fail"
        elif "max" in s and val > s["max"]:
            verdict = "fail"
        if s.get("no_significant_drop") and a:
            keys = sorted(set(a) & set(b))
            if keys:
                d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                   n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
                note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}] n={len(keys)}"
                if hi < 0:
                    verdict = "fail"
            else:
                verdict, note = "missing", "no items shared with the baseline"
        if verdict != "ok" and s.get("blocking"):
            blocked.append(sid)
        th = {k: s[k] for k in ("min", "max", "no_significant_drop") if k in s}
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if s.get("blocking") else "warn")
        lines.append(f"  {sid:<38} {val!s:<8} {json.dumps(th):<40} {note:<34} {label}")
    return blocked, lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
