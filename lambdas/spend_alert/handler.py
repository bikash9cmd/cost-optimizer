"""
Checks AWS spend via Cost Explorer and posts to Slack if it's over the
configured threshold. Runs once a day off an EventBridge schedule (see
infra/template.yaml) - Cost Explorer data also only updates a few times a
day, so polling more often than that just burns API calls for the same
numbers.

Two checks:
  1. Month-to-date total vs MONTHLY_BUDGET_USD
  2. Yesterday's spend vs DAILY_THRESHOLD_USD (catches a sudden spike before
     it eats the whole month's budget)
"""

import os
import boto3
from datetime import date, timedelta

from common.slack import post_message

ce = boto3.client("ce")



def _read_positive_float(name: str, default: str) -> float:
    """Read a USD threshold from the environment. Fails loudly on a bad
    value instead of half-working: a zero budget used to raise
    ZeroDivisionError mid-run (so no alert went out at all), and a typo like
    "$100" gave a bare float() error that didn't say which variable was
    wrong."""
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number in USD, got {raw!r}") from None
    if not value > 0:  # also rejects NaN
        raise ValueError(f"{name} must be greater than 0, got {raw!r}")
    return value


MONTHLY_BUDGET_USD = _read_positive_float("MONTHLY_BUDGET_USD", "100")
DAILY_THRESHOLD_USD = _read_positive_float("DAILY_THRESHOLD_USD", "10")


def _get_month_to_date_cost() -> float:
    today = date.today()
    start = today.replace(day=1).isoformat()
    end = today.isoformat()

    # Cost Explorer's End date is exclusive, and won't accept Start == End,
    # so on the 1st of the month we just bump End forward by a day
    if start == end:
        end = (today + timedelta(days=1)).isoformat()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="MONTHLY",
        Metrics=["UnblendedCost"],
    )
    return float(resp["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"])


def _get_yesterday_cost() -> float:
    yesterday = date.today() - timedelta(days=1)
    start = yesterday.isoformat()
    end = date.today().isoformat()

    resp = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
    )
    return float(resp["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"])


def handler(event, context):
    mtd_cost = _get_month_to_date_cost()
    yesterday_cost = _get_yesterday_cost()

    alerts = []

    if mtd_cost >= MONTHLY_BUDGET_USD:
        pct = (mtd_cost / MONTHLY_BUDGET_USD) * 100
        alerts.append(
            f":rotating_light: Month-to-date spend is *${mtd_cost:.2f}*, "
            f"{pct:.0f}% of the ${MONTHLY_BUDGET_USD:.0f} budget."
        )

    if yesterday_cost >= DAILY_THRESHOLD_USD:
        alerts.append(
            f":warning: Yesterday's spend was *${yesterday_cost:.2f}*, "
            f"over the ${DAILY_THRESHOLD_USD:.0f} daily threshold."
        )

    if alerts:
        post_message("\n".join(alerts))
    else:
        # not posting a "all good" message every single day, that's just
        # noise - logging it is enough
        print(f"spend within thresholds. mtd=${mtd_cost:.2f} yesterday=${yesterday_cost:.2f}")

    return {
        "month_to_date_usd": round(mtd_cost, 2),
        "yesterday_usd": round(yesterday_cost, 2),
        "alerted": bool(alerts),
    }
