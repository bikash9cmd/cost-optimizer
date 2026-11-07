"""
Tests for spend_alert - the threshold checks, the Cost Explorer date
windows and the env var validation. Cost Explorer and Slack are replaced
with small fakes, so these run without AWS credentials (the region env var
from the README is still needed for the boto3.client() call at import).
"""

import importlib
import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas"))

import spend_alert.handler as spend_alert  # noqa: E402


class FakeCostExplorer:
    """Returns a fixed amount per granularity and records every request."""

    def __init__(self, monthly: float, daily: float):
        self.amounts = {"MONTHLY": monthly, "DAILY": daily}
        self.calls = []

    def get_cost_and_usage(self, TimePeriod, Granularity, Metrics):
        self.calls.append({"TimePeriod": TimePeriod, "Granularity": Granularity})
        amount = str(self.amounts[Granularity])
        return {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": amount, "Unit": "USD"}}}]}


def _fixed_date(today: date):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return today

    return FixedDate


@pytest.fixture
def run(monkeypatch):
    """Run the handler with fake Cost Explorer amounts on a given day.
    Returns (result, slack_messages, cost_explorer_calls)."""

    def _run(monthly, daily, today=date(2026, 3, 15), budget=100.0, daily_threshold=10.0):
        fake_ce = FakeCostExplorer(monthly, daily)
        posted = []
        monkeypatch.setattr(spend_alert, "ce", fake_ce)
        monkeypatch.setattr(spend_alert, "post_message", lambda text, blocks=None: posted.append(text))
        monkeypatch.setattr(spend_alert, "date", _fixed_date(today))
        monkeypatch.setattr(spend_alert, "MONTHLY_BUDGET_USD", budget)
        monkeypatch.setattr(spend_alert, "DAILY_THRESHOLD_USD", daily_threshold)
        result = spend_alert.handler({}, None)
        return result, posted, fake_ce.calls

    return _run


def test_no_post_when_under_both_thresholds(run):
    result, posted, _ = run(monthly=40.0, daily=2.5)
    assert posted == []
    assert result == {"month_to_date_usd": 40.0, "yesterday_usd": 2.5, "alerted": False}


def test_alerts_when_month_to_date_reaches_budget(run):
    result, posted, _ = run(monthly=125.0, daily=2.0)
    assert result["alerted"] is True
    assert len(posted) == 1
    assert "$125.00" in posted[0]
    assert "125% of the $100 budget" in posted[0]
    assert "daily threshold" not in posted[0]


def test_alerts_on_daily_spike_even_under_monthly_budget(run):
    result, posted, _ = run(monthly=30.0, daily=12.34)
    assert result["alerted"] is True
    assert "Yesterday's spend was *$12.34*" in posted[0]
    assert "budget" not in posted[0]


def test_both_alerts_go_in_one_message(run):
    _, posted, _ = run(monthly=150.0, daily=20.0)
    assert len(posted) == 1
    assert "Month-to-date" in posted[0] and "Yesterday" in posted[0]


def test_date_windows_on_a_normal_day(run):
    _, _, calls = run(monthly=1.0, daily=1.0, today=date(2026, 3, 15))
    monthly, daily = calls
    # Cost Explorer's End is exclusive, so "to date" ends at today
    assert monthly == {"TimePeriod": {"Start": "2026-03-01", "End": "2026-03-15"}, "Granularity": "MONTHLY"}
    assert daily == {"TimePeriod": {"Start": "2026-03-14", "End": "2026-03-15"}, "Granularity": "DAILY"}


def test_date_windows_on_first_of_month(run):
    # Start == End is rejected by Cost Explorer, so End is bumped a day;
    # "yesterday" is the last day of the previous month
    _, _, calls = run(monthly=1.0, daily=1.0, today=date(2026, 3, 1))
    monthly, daily = calls
    assert monthly["TimePeriod"] == {"Start": "2026-03-01", "End": "2026-03-02"}
    assert daily["TimePeriod"] == {"Start": "2026-02-28", "End": "2026-03-01"}


@pytest.mark.parametrize("raw, expected", [("250", 250.0), ("0.5", 0.5), (" 42 ", 42.0)])
def test_read_positive_float_accepts_valid_values(monkeypatch, raw, expected):
    monkeypatch.setenv("SOME_THRESHOLD", raw)
    assert spend_alert._read_positive_float("SOME_THRESHOLD", "1") == expected


def test_read_positive_float_uses_default_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_THRESHOLD", raising=False)
    assert spend_alert._read_positive_float("SOME_THRESHOLD", "10") == 10.0


@pytest.mark.parametrize("raw", ["0", "-5", "nan"])
def test_read_positive_float_rejects_non_positive(monkeypatch, raw):
    monkeypatch.setenv("SOME_THRESHOLD", raw)
    with pytest.raises(ValueError, match="SOME_THRESHOLD must be greater than 0"):
        spend_alert._read_positive_float("SOME_THRESHOLD", "1")


@pytest.mark.parametrize("raw", ["$100", "abc", ""])
def test_read_positive_float_rejects_non_numbers(monkeypatch, raw):
    monkeypatch.setenv("SOME_THRESHOLD", raw)
    with pytest.raises(ValueError, match="SOME_THRESHOLD must be a number in USD"):
        spend_alert._read_positive_float("SOME_THRESHOLD", "1")


def test_zero_budget_fails_at_import_with_clear_error(monkeypatch):
    # used to import fine and then raise ZeroDivisionError on the first
    # day there was any spend, so no alert was ever sent
    monkeypatch.setenv("MONTHLY_BUDGET_USD", "0")
    try:
        with pytest.raises(ValueError, match="MONTHLY_BUDGET_USD must be greater than 0"):
            importlib.reload(spend_alert)
    finally:
        monkeypatch.delenv("MONTHLY_BUDGET_USD")
        importlib.reload(spend_alert)
