"""Automated guards for CLAUDE.md §17 non-negotiables that grep can enforce."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
ROOT = SRC.parent


def _py_files(base: Path):
    return [p for p in base.rglob("*.py")]


def test_fyers_sdk_only_imported_inside_fyers_package():
    """§17.6: all Fyers access goes through src/fyers (client wraps the shared
    rate limiter). No other module may import the SDK."""
    offenders = []
    for p in _py_files(SRC):
        if (SRC / "fyers") in p.parents:
            continue
        text = p.read_text()
        if re.search(r"^\s*(import|from)\s+fyers_apiv3", text, re.M):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"fyers_apiv3 imported outside src/fyers/: {offenders}"


def test_no_accuracy_language_in_code_or_docs():
    """§17.1: no "accuracy" language anywhere — code, UI strings, logs.
    (The ML metric vocabulary here is calibrated confidence + precision.)"""
    banned = re.compile(r"accurac|win[- ]rate guarantee|proven returns", re.I)
    offenders = []
    for p in _py_files(SRC) + [ROOT / "README.md"]:
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if banned.search(line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, "banned language found:\n" + "\n".join(offenders)


def test_no_naive_datetime_now_in_src():
    """§17.9: naive datetimes are bugs. datetime.now()/utcnow() without a
    timezone must not appear; use src.timeutil helpers."""
    bad_call = re.compile(r"datetime\.now\(\)|datetime\.utcnow\(|\bdate\.today\(")
    offenders = []
    for p in _py_files(SRC):
        for i, line in enumerate(p.read_text().splitlines(), 1):
            if bad_call.search(line):
                offenders.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, "naive datetime usage:\n" + "\n".join(offenders)


def test_order_placement_stays_unwired():
    """§17.8: V1 places no orders. No order-placement endpoints anywhere."""
    banned = re.compile(r"place_order|placeorder|orders/sync|modify_order|cancel_order", re.I)
    offenders = []
    for p in _py_files(SRC):
        if banned.search(p.read_text()):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"order-placement code found (V2-gated): {offenders}"
