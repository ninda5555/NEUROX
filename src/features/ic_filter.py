"""Feature hygiene (CLAUDE.md §6.1): Information Coefficient per feature at
training time; drop |IC| below threshold or pairwise correlation > 0.9
(keeping the higher-|IC| feature). Output is a report — P2 trains on the
kept list; nothing is silently deleted."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

IC_MIN = 0.01
CORR_MAX = 0.90


@dataclass
class ICReport:
    mode: str
    n_rows: int
    ic: dict[str, float] = field(default_factory=dict)         # feature -> IC
    dropped_low_ic: list[str] = field(default_factory=list)
    dropped_corr: list[tuple[str, str]] = field(default_factory=list)  # (dropped, kept_because)
    kept: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "n_rows": self.n_rows, "ic": self.ic,
                "dropped_low_ic": self.dropped_low_ic,
                "dropped_corr": [list(x) for x in self.dropped_corr],
                "kept": self.kept}


def spearman_ic(x: pd.Series, y: pd.Series) -> float:
    m = x.notna() & y.notna()
    if m.sum() < 50 or y[m].nunique() < 2 or x[m].nunique() < 2:
        return float("nan")
    return float(x[m].rank().corr(y[m].rank()))


def compute_ic_report(frame: pd.DataFrame, feature_cols: list[str], label_col: str,
                      mode: str, ic_min: float = IC_MIN,
                      corr_max: float = CORR_MAX) -> ICReport:
    """`frame` must already have masked/unlabeled rows removed."""
    rep = ICReport(mode=mode, n_rows=len(frame))
    y = frame[label_col]
    for f in feature_cols:
        rep.ic[f] = spearman_ic(frame[f], y)

    # low-|IC| drop (NaN IC counts as no-signal -> dropped)
    survivors = []
    for f in feature_cols:
        v = rep.ic[f]
        if np.isnan(v) or abs(v) < ic_min:
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


def format_report(rep: ICReport) -> str:
    lines = [f"IC report — {rep.mode} ({rep.n_rows:,} labeled rows)",
             f"{'feature':<18}{'IC':>8}  status"]
    for f, v in sorted(rep.ic.items(), key=lambda kv: -(abs(kv[1]) if kv[1] == kv[1] else -1)):
        if f in rep.kept:
            status = "kept"
        elif f in rep.dropped_low_ic:
            status = "dropped (|IC| < threshold)"
        else:
            keeper = next((k for d, k in rep.dropped_corr if d == f), "?")
            status = f"dropped (corr>0.9 with {keeper})"
        vs = f"{v:+.4f}" if v == v else "   nan"
        lines.append(f"{f:<18}{vs:>8}  {status}")
    return "\n".join(lines)
