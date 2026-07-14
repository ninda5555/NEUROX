"""SHAP -> plain English (CLAUDE.md §6.2). Every signal stores its top-6
SHAP contributors; templates map feature -> sentence. Positive SHAP pushes
toward "setups like this worked historically"."""

from __future__ import annotations

import numpy as np
import pandas as pd

# (supporting sentence, opposing sentence) per feature
SENTENCES: dict[str, tuple[str, str]] = {
    "vwap_dist_atr": ("Price is holding well above VWAP.", "Price is trading below VWAP."),
    "orb_position": ("Trading near the top of the opening range.", "Sitting low in the opening range."),
    "trend_agree_15m": ("The 15-minute trend agrees with the setup.", "The 15-minute trend disagrees."),
    "cum_vol_ratio": ("Session volume is running ahead of its usual pace.", "Session volume is running light."),
    "vol_zscore": ("Volume is surging versus the 20-bar average.", "Volume is quiet versus average."),
    "rsi_14": ("RSI is constructive.", "RSI is working against the setup."),
    "macd_hist": ("MACD histogram is positive.", "MACD histogram is negative."),
    "macd_slope": ("MACD momentum is improving.", "MACD momentum is fading."),
    "atr_pct": ("Volatility is elevated — bigger range to work with.", "Volatility is very low."),
    "atr14_pct": ("Daily volatility is supportive.", "Daily volatility profile is unhelpful."),
    "gk_vol": ("Realized volatility is elevated.", "Realized volatility is muted."),
    "gk_vol_trend": ("Volatility is compressing.", "Volatility is expanding."),
    "gap_pct": ("The opening gap favors the setup.", "The opening gap works against it."),
    "rs_nifty": ("Showing relative strength versus NIFTY.", "Lagging NIFTY."),
    "rs_nifty_20d": ("Leading NIFTY over the last month.", "Trailing NIFTY over the last month."),
    "rs_sector": ("Leading its sector.", "Lagging its sector."),
    "dma_alignment": ("Moving averages are stacked bullishly.", "Moving averages are not aligned."),
    "dist_20dma": ("Holding above the 20-day average.", "Below the 20-day average."),
    "dist_50dma": ("Holding above the 50-day average.", "Below the 50-day average."),
    "dist_200dma": ("Above the long-term 200-day average.", "Below the 200-day average."),
    "dist_52w_high": ("Close to its 52-week high.", "Far below its 52-week high."),
    "weekly_rsi": ("The weekly trend confirms.", "The weekly trend does not confirm."),
    "dow": ("Day-of-week pattern is favorable.", "Day-of-week pattern is unfavorable."),
    "tod_frac": ("Time of day favors this setup.", "Late-session timing works against it."),
    "ofi_top": ("Order flow is leaning with the trade.", "Order flow is leaning against the trade."),
    "direction": ("Trade direction fits current conditions.", "Trade direction fights current conditions."),
    "regime_vix": ("The volatility regime has suited setups like this.", "The volatility regime has hurt setups like this."),
    "regime_vix_rising": ("VIX trend supports the setup.", "Rising fear works against it."),
    "regime_nifty_above_50": ("NIFTY's medium-term trend helps.", "NIFTY's medium-term trend hurts."),
    "regime_nifty_above_200": ("NIFTY's long-term trend helps.", "NIFTY's long-term trend hurts."),
    "regime_breadth": ("Market breadth is on the trade's side.", "Market breadth is against the trade."),
}


def sentence_for(feature: str, shap_value: float) -> str:
    pos, neg = SENTENCES.get(feature, (f"{feature} supports the setup.",
                                       f"{feature} works against the setup."))
    return pos if shap_value >= 0 else neg


def _shap_row(booster, X: pd.DataFrame) -> np.ndarray:
    import shap as shap_lib
    sv = shap_lib.TreeExplainer(booster).shap_values(X)
    vals = sv[1] if isinstance(sv, list) else sv       # binary: class-1 column
    return np.asarray(vals)[0]


def top_contributors(booster, X_row: pd.DataFrame, feature_names: list[str],
                     k: int = 6) -> list[dict]:
    """Top-k SHAP contributors for one row -> [{feature, value, shap, sentence}].
    For a bagged ensemble (T10) the attribution is the MEMBER MEAN — the
    same aggregation as the score itself, so the explanation explains the
    number the user actually sees."""
    from src.models.ensemble import BaggedBooster
    from src.models.meta import MetaPipeline
    if isinstance(booster, MetaPipeline):
        # the meta is a veto/sizing filter; the PRIMARY's drivers are the
        # trade's reasoning, so that's what the user reads (T11)
        booster = booster.primary
    X = X_row[feature_names].astype(np.float32)
    if isinstance(booster, BaggedBooster):
        row = np.mean([_shap_row(m, X) for m in booster.members], axis=0)
    else:
        row = _shap_row(booster, X)
    order = np.argsort(-np.abs(row))[:k]
    out = []
    for rank, i in enumerate(order):
        f = feature_names[i]
        s = float(row[i])
        raw = X_row.iloc[0][f]
        sent = sentence_for(f, s)
        if rank == 0:
            sent = sent.rstrip(".") + " (strongest factor)."
        out.append({"feature": f,
                    "value": None if pd.isna(raw) else round(float(raw), 4),
                    "shap": round(s, 4), "sentence": sent})
    return out
