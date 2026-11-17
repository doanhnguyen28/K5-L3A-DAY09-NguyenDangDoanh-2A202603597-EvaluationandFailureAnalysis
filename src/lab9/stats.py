from __future__ import annotations

from collections.abc import Sequence

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    if len(human) != len(judge):
        raise ValueError(f"length mismatch: {len(human)} human vs {len(judge)} judge labels")
    out = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for h, j in zip(human, judge):
        h_pos, j_pos = h == POS, j == POS
        if j_pos and h_pos:
            out["tp"] += 1
        elif j_pos:
            out["fp"] += 1
        elif h_pos:
            out["fn"] += 1
        else:
            out["tn"] += 1
    return out


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    c = confusion(human, judge)
    precision = c["tp"] / (c["tp"] + c["fp"]) if c["tp"] + c["fp"] else 0.0
    recall = c["tp"] / (c["tp"] + c["fn"]) if c["tp"] + c["fn"] else 0.0
    return precision, recall


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    c = confusion(human, judge)
    n = sum(c.values())
    if n == 0:
        raise ValueError("kappa undefined: no labels")
    p_o = (c["tp"] + c["tn"]) / n
    human_pos = (c["tp"] + c["fn"]) / n
    judge_pos = (c["tp"] + c["fp"]) / n
    p_e = human_pos * judge_pos + (1 - human_pos) * (1 - judge_pos)
    if p_e >= 1.0:
        raise ValueError("kappa undefined: chance agreement is 1 (constant labels)")
    return (p_o - p_e) / (1 - p_e)


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    denom = sensitivity + specificity - 1
    if denom <= 0:
        raise ValueError("Rogan-Gladen undefined: judge is no better than chance (sens + spec <= 1)")
    return min(1.0, max(0.0, (observed_rate + specificity - 1) / denom))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    import numpy as np
    from scipy.stats import bootstrap

    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    if x.shape != y.shape or x.ndim != 1 or x.size == 0:
        raise ValueError("paired_bootstrap needs two non-empty sequences of the same length")
    if np.array_equal(x, y):
        return (0.0, 0.0, 0.0)
    diff = float(x.mean() - y.mean())
    res = bootstrap((x, y), lambda u, v, axis=-1: u.mean(axis=axis) - v.mean(axis=axis),
                    paired=True, vectorized=True, n_resamples=n_resamples, method="percentile",
                    confidence_level=confidence, rng=np.random.default_rng(rng))
    lo, hi = res.confidence_interval
    return (diff, float(lo), float(hi))


def _check_nck(n: int, c: int, k: int) -> tuple[int, int, int]:
    """Validate (n trials, c passing, k drawn). bool is rejected: True/False are not counts."""
    for name, v in (("n", n), ("c", c), ("k", k)):
        if isinstance(v, bool) or not isinstance(v, (int,)) and not (isinstance(v, float) and v.is_integer()):
            raise ValueError(f"{name} must be an integer count, got {v!r}")
    n, c, k = int(n), int(c), int(k)
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n}")
    if not 0 <= c <= n:
        raise ValueError(f"c must be in [0, n={n}], got {c}")
    if not 1 <= k <= n:
        raise ValueError(f"k must be in [1, n={n}], got {k}")
    return n, c, k


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError on bad (n, c, k)."""
    from math import comb

    n, c, k = _check_nck(n, c, k)
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError on bad (n, c, k).
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    from math import comb

    n, c, k = _check_nck(n, c, k)
    return comb(c, k) / comb(n, k)
