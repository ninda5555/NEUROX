"""Feature hygiene (CLAUDE.md §6.1): Information Coefficient per feature at
training time; drop low-|IC| or pairwise-correlated features (keeping the
higher-|IC| one). Output is a report — P2 trains on the kept list; nothing
is silently deleted.

CROSS-SECTIONAL IC (corrected 2026-08-01, root-cause fix)
---------------------------------------------------------
The original implementation pooled every symbol-bar into one correlation.
That silently conflates two different questions:

    "are setups more likely to win on some DAYS than others?"   (market timing)
    "which STOCK should I pick right now?"                      (selection)

A scanner only ever answers the second one. It scores ~860 symbols at a
single instant and ranks them against each other, so the only thing that can
possibly help is a feature that VARIES ACROSS SYMBOLS AT THAT INSTANT.

Pooled IC rewarded features that do the opposite. `regime_breadth` takes 155
distinct values across 4.57M rows (it is one number per day, shared by every
symbol); pooled IC scored it +0.0786 — the strongest feature in the report —
while its true cross-sectional IC is +0.0087. The model then loaded 25% of
its gain onto a column that is mathematically incapable of separating one
stock from another, and effectively memorised day-level base rates instead.
`tod_frac` is worse: it is constant within a bar by construction, so its
cross-sectional IC does not exist at all, yet pooled IC scored it -0.0453.

So IC is now computed the standard way (Grinold & Kahn): Spearman
correlation WITHIN each bar, then averaged across bars, with a t-stat on the
resulting IC time series (mean / (std/sqrt(n_bars))). A market-wide constant
has no within-bar variance, yields NaN every bar, and drops out on its own —
the measurement itself now enforces what §6.1 always intended.

Pooled IC is still computed and reported alongside, deliberately: the gap
between the two columns is the diagnostic that caught this, and keeping it
visible stops the same mistake being made again.

A feature must clear BOTH bars to survive:
  * |mean cross-sectional IC| >= ic_min   — economic size
  * |t-stat| >= t_min                     — not an artifact of noisy bars
Passing both means "measurably better than random", NOT "tradeable after
costs" — that judgement belongs to the expectancy gate, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

IC_MIN = 0.01
CORR_MAX = 0.90
T_MIN = 3.0          # |t| >= 3 on the IC time series
MIN_NAMES_PER_BAR = 30   # a cross-section needs real breadth to mean anything


@dataclass
class ICReport:
    mode: str
    n_rows: int
    ic: dict[str, float] = field(default_factory=dict)          # cross-sectional mean IC
    ic_t: dict[str, float] = field(default_factory=dict)        # t-stat of the IC series
    ic_std: dict[str, float] = field(default_factory=dict)      # bar-to-bar IC volatility
    ic_bars: dict[str, int] = field(default_factory=dict)       # bars the IC was measurable on
    ic_pooled: dict[str, float] = field(default_factory=dict)   # OLD measure, kept for contrast
    n_bars: int = 0
    overlap_bars: int = 1      # Newey-West lag used for the t-stats
    dropped_low_ic: list[str] = field(default_factory=list)
    dropped_corr: list[tuple[str, str]] = field(default_factory=list)  # (dropped, kept_because)
    kept: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "n_rows": self.n_rows, "n_bars": self.n_bars,
                "overlap_bars": self.overlap_bars,
                "ic": self.ic, "ic_t": self.ic_t, "ic_std": self.ic_std,
                "ic_bars": self.ic_bars, "ic_pooled": self.ic_pooled,
                "dropped_low_ic": self.dropped_low_ic,
                "dropped_corr": [list(x) for x in self.dropped_corr],
                "kept": self.kept}


def spearman_ic(x: pd.Series, y: pd.Series) -> float:
    """Pooled IC — every row in one correlation. Retained ONLY as the
    contrast column in the report (see module docstring); never used for
    selection."""
    m = x.notna() & y.notna()
    if m.sum() < 50 or y[m].nunique() < 2 or x[m].nunique() < 2:
        return float("nan")
    return float(x[m].rank().corr(y[m].rank()))


def _newey_west_se(ics: np.ndarray, lag: int) -> float:
    """HAC standard error of the mean of an autocorrelated series.

    The IC series is NOT independent across bars: a triple-barrier label
    started at bar t and one started at t+1 resolve over almost the same
    path, so their ICs move together. Treating them as independent inflates
    t by roughly sqrt(overlap) — for 10-session swing labels that is ~3x,
    which is the difference between "12 features are significant" and
    "3 are". Bartlett-kernel Newey-West with lag = the overlap horizon.
    """
    n = len(ics)
    if n < 2:
        return float("nan")
    x = ics - ics.mean()
    var = float(x @ x) / n
    lag = max(0, min(int(lag), n - 1))
    for k in range(1, lag + 1):
        w = 1.0 - k / (lag + 1.0)
        var += 2.0 * w * float(x[k:] @ x[:-k]) / n
    if var <= 0:
        return float("nan")
    return float(np.sqrt(var / n))


def cross_sectional_ic(frame: pd.DataFrame, feature: str, label_col: str,
                       ts_col: str = "ts",
                       min_names: int = MIN_NAMES_PER_BAR,
                       overlap_bars: int = 1) -> tuple[float, float, float, int]:
    """Spearman IC within each bar, averaged across bars.

    Returns (mean_ic, std_ic, t_stat, n_bars_measured). All NaN/0 when the
    feature never varies within a bar — which is the correct answer for a
    market-wide constant, not an error.

    Vectorised: rank within bar, then per-bar Pearson on the ranks via
    groupby aggregates (Spearman == Pearson on ranks). groupby.apply over
    ~5k bars x ~20 features was too slow to run at every retrain.
    """
    d = frame[[ts_col, feature, label_col]].dropna()
    if d.empty:
        return float("nan"), float("nan"), float("nan"), 0

    g = d.groupby(ts_col, sort=False)
    # bars with enough names AND actual variation in the feature
    size = g[feature].transform("size")
    nun = g[feature].transform("nunique")
    d = d[(size >= min_names) & (nun > 1)]
    if d.empty:
        return float("nan"), float("nan"), float("nan"), 0

    g = d.groupby(ts_col, sort=False)
    x = g[feature].rank()
    y = g[label_col].rank()
    d = d.assign(_x=x, _y=y, _xy=x * y, _xx=x * x, _yy=y * y)
    a = d.groupby(ts_col, sort=False).agg(
        n=("_x", "size"), sx=("_x", "sum"), sy=("_y", "sum"),
        sxy=("_xy", "sum"), sxx=("_xx", "sum"), syy=("_yy", "sum"))
    cov = a.sxy - a.sx * a.sy / a.n
    vx = a.sxx - a.sx ** 2 / a.n
    vy = a.syy - a.sy ** 2 / a.n
    denom = np.sqrt(vx * vy)
    ics = (cov / denom.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).dropna()
    if ics.empty:
        return float("nan"), float("nan"), float("nan"), 0

    mean = float(ics.mean())
    sd = float(ics.std())
    n = int(len(ics))
    se = _newey_west_se(ics.to_numpy(), overlap_bars - 1)
    t = float(mean / se) if se == se and se > 0 else float("nan")
    return mean, sd, t, n


def label_overlap_bars(frame: pd.DataFrame, mode: str, ts_col: str = "ts") -> int:
    """How many CONSECUTIVE bars carry overlapping label windows.

    SWING: a 10-session barrier walk, one bar per session -> 10.
    INTRADAY: labels resolve by 15:15 the same day, so every bar in a
    session overlaps every later bar in it -> the session's bar count,
    measured from the data rather than assumed.
    """
    if mode == "SWING":
        return 10
    days = pd.DatetimeIndex(frame[ts_col]).normalize()
    per_day = frame.groupby(days)[ts_col].nunique()
    return int(per_day.median()) if len(per_day) else 1


def compute_ic_report(frame: pd.DataFrame, feature_cols: list[str], label_col: str,
                      mode: str, ic_min: float = IC_MIN,
                      corr_max: float = CORR_MAX, t_min: float = T_MIN,
                      ts_col: str = "ts", overlap_bars: int | None = None) -> ICReport:
    """`frame` must already have masked/unlabeled rows removed, and must
    carry `ts_col` so the cross-section can be formed.

    `overlap_bars` sets the Newey-West lag used for the IC t-stat; leave it
    None to infer it from the mode's label horizon (see label_overlap_bars).
    Pass 1 to disable the correction — only sensible for non-overlapping
    labels, which this project does not have.
    """
    rep = ICReport(mode=mode, n_rows=len(frame))
    y = frame[label_col]
    if ts_col not in frame.columns:
        raise ValueError(
            f"cross-sectional IC needs a '{ts_col}' column to group the "
            "cross-section by; got columns "
            f"{sorted(frame.columns)[:12]}…")
    rep.n_bars = int(frame[ts_col].nunique())
    if overlap_bars is None:
        overlap_bars = label_overlap_bars(frame, mode, ts_col)
    rep.overlap_bars = int(overlap_bars)

    for f in feature_cols:
        mean, sd, t, nb = cross_sectional_ic(frame, f, label_col, ts_col,
                                             overlap_bars=overlap_bars)
        rep.ic[f] = mean
        rep.ic_std[f] = sd
        rep.ic_t[f] = t
        rep.ic_bars[f] = nb
        rep.ic_pooled[f] = spearman_ic(frame[f], y)

    # size AND significance; NaN (incl. market-wide constants) counts as no signal
    survivors = []
    for f in feature_cols:
        v, t = rep.ic[f], rep.ic_t[f]
        if np.isnan(v) or abs(v) < ic_min or np.isnan(t) or abs(t) < t_min:
            rep.dropped_low_ic.append(f)
        else:
            survivors.append(f)

    # pairwise-correlation prune: keep the higher-|IC| of any pair > corr_max
    if survivors:
        corr = frame[survivors].corr().abs()
        by_strength = sorted(survivors, key=lambda f: -abs(rep.ic[f]))
        kept: list[str] = []
        for f in by_strength:
            clash = next((k for k in kept if corr.loc[f, k] > corr_max), None)
            if clash is None:
                kept.append(f)
            else:
                rep.dropped_corr.append((f, clash))
        rep.kept = [f for f in survivors if f in kept]
    return rep


def _num(v, spec: str = "+.4f") -> str:
    return f"{v:{spec}}" if v == v else "nan"


def format_report(rep: ICReport) -> str:
    lines = [
        f"IC report — {rep.mode} ({rep.n_rows:,} labeled rows, {rep.n_bars:,} bars)",
        "cross-sectional IC (within-bar, averaged) is what selects; 'pooled' is the",
        "OLD conflated measure, shown only so the gap stays visible.",
        f"t-stats are Newey-West with lag {rep.overlap_bars} (overlapping labels: "
        "an uncorrected t is inflated ~sqrt(overlap)).",
        "",
        f"{'feature':<24}{'xs IC':>9}{'t':>8}{'bars':>8}{'pooled':>9}  status"]
    for f, v in sorted(rep.ic.items(),
                       key=lambda kv: -(abs(kv[1]) if kv[1] == kv[1] else -1)):
        if f in rep.kept:
            status = "kept"
        elif f in rep.dropped_low_ic:
            t = rep.ic_t.get(f, float("nan"))
            if v != v:
                status = "dropped (no within-bar variance — market-wide constant)"
            elif abs(v) < IC_MIN:
                status = "dropped (|IC| below threshold)"
            elif t != t or abs(t) < T_MIN:
                status = "dropped (not significant)"
            else:
                status = "dropped"
        else:
            keeper = next((k for d, k in rep.dropped_corr if d == f), "?")
            status = f"dropped (corr>0.9 with {keeper})"
        lines.append(
            f"{f:<24}{_num(v):>9}{_num(rep.ic_t.get(f, float('nan')), '+.1f'):>8}"
            f"{rep.ic_bars.get(f, 0):>8,}"
            f"{_num(rep.ic_pooled.get(f, float('nan'))):>9}  {status}")
    return "\n".join(lines)
