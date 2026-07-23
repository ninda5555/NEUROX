"""The Jul-2026 outage class: jobs missing from the schedule, or dying
quietly on an expired token with no recovery path. These tests pin the
schedule's shape and the skip-don't-crash behavior."""
import datetime as dt
import logging

import pytest

from src.jobs import scheduler as sched


class _Cfg(dict):
    def __getitem__(self, k):
        if k == "training.retrain_schedule":
            return dict.get(self, k, "weekly")
        if k == "fyers.auto_login":
            return dict.get(self, k, False)
        return dict.get(self, k)


def _job_names(s):
    return [j.func.__name__ for j in s.get_jobs()]


def test_schedule_includes_intraday_scan_catchup_and_heartbeat():
    s = sched.build_scheduler(_Cfg())
    names = _job_names(s)
    for required in ("job_intraday_scan", "job_morning_catchup",
                     "job_heartbeat", "job_swing_scan", "job_candle_topup",
                     "job_universe_rebuild",
                     "job_daily_backfill_universe_candles",
                     "job_nightly_backup", "job_weekly_retrain",
                     "job_weekly_digest", "job_daily_auto_login"):
        assert required in names, f"{required} missing from schedule"


def test_intraday_scan_fires_repeatedly_during_market_hours():
    """A Tuesday: passes must exist between 09:25 and 15:30 IST, ~every 20m."""
    s = sched.build_scheduler(_Cfg())
    job = next(j for j in s.get_jobs() if j.func.__name__ == "job_intraday_scan")
    t = dt.datetime(2026, 7, 21, 0, 0, tzinfo=sched.IST)  # Tue
    fires = []
    end = t.replace(hour=23)
    while t < end:
        nxt = job.trigger.get_next_fire_time(None, t)
        if nxt is None or nxt >= end:
            break
        fires.append(nxt)
        t = nxt + dt.timedelta(seconds=1)
    in_window = [f for f in fires
                 if sched.INTRADAY_FIRST_PASS <= f.time() <= sched.MARKET_CLOSE]
    assert len(in_window) >= 15  # 09:40..15:20 at 20-min cadence
    assert any(f.time() >= dt.time(15, 15) for f in in_window)  # square-off pass


def test_intraday_scan_skips_cleanly_outside_market_window(monkeypatch):
    called = []
    monkeypatch.setattr(sched, "_ctx", lambda: called.append(1))
    monkeypatch.setattr(sched, "now_ist",
                        lambda: dt.datetime(2026, 7, 21, 8, 0, tzinfo=sched.IST))
    sched.job_intraday_scan()
    assert not called, "must not touch the DB outside 09:25-15:30"


def test_intraday_scan_logs_skip_on_expired_token(monkeypatch, caplog):
    monkeypatch.setattr(sched, "now_ist",
                        lambda: dt.datetime(2026, 7, 21, 11, 0, tzinfo=sched.IST))
    monkeypatch.setattr(sched, "_ctx", lambda: ({}, None, None))

    def boom(cfg, conn):
        raise sched.auth.NeedsReauth("token expired")
    monkeypatch.setattr(sched, "_client", boom)
    with caplog.at_level(logging.ERROR):
        sched.job_intraday_scan()   # must not raise
    assert any("no valid token" in r.message for r in caplog.records)


@pytest.mark.parametrize("job", [sched.job_candle_topup,
                                 sched.job_daily_backfill_universe_candles,
                                 sched.job_morning_catchup])
def test_token_needing_jobs_skip_cleanly_on_expired_token(job, monkeypatch, caplog):
    monkeypatch.setattr(sched, "_ctx", lambda: ({}, None, None))

    def boom(cfg, conn):
        raise sched.auth.NeedsReauth("token expired")
    monkeypatch.setattr(sched, "_client", boom)
    with caplog.at_level(logging.ERROR):
        job()   # must not raise — §17.11
    assert any("no valid token" in r.message for r in caplog.records)
