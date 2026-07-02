"""
Takes the idle_detector's findings and prices them out against rough
on-demand rates to get the "if we cleaned this up, what would we save"
number. This is what produced the ~20% figure mentioned in the CV bullet.

Rates below are ballpark us-east-1 on-demand pricing as of late 2025, NOT
pulled live from the Pricing API - wanted this script to run without
needing pricing API permissions on top of everything else. If accuracy
matters more than convenience, swap _RATE_TABLE for a real
boto3 pricing client call.

Usage:
    python simulate_savings.py --csv path/to/idle-report.csv --current-monthly-bill 250
"""

import csv
import argparse

# USD/month, rough on-demand, NOT live pricing - see docstring
_RATE_TABLE = {
    "ec2_hourly": {
        "t3.micro": 0.0104,
        "t3.small": 0.0208,
        "t3.medium": 0.0416,
        "t3.large": 0.0832,
        "m5.large": 0.096,
        "m5.xlarge": 0.192,
    },
    "ebs_per_gb_month": {
        "gp2": 0.10,
        "gp3": 0.08,
        "io1": 0.125,
    },
    "eip_unassociated_hourly": 0.005,  # AWS charges for EIPs NOT attached to a running instance
    "ec2_default_hourly": 0.05,        # fallback for instance types not in the table above
}

HOURS_PER_MONTH = 730


def _ec2_stopped_savings(instance_type: str) -> float:
    """Note: a STOPPED instance isn't actually accruing compute charges
    (you only pay for any attached EBS storage while stopped) - the real
    saving here is from finally terminating it and reclaiming the EBS
    storage + removing the temptation to leave it stopped forever instead
    of actually deciding whether it's needed. We price it as if it were
    running, to represent 'this is dead weight that should be decided on',
    not as a literal current monthly charge - documented here so it's not
    mistaken for cost the instance is actively generating."""
    rate = _RATE_TABLE["ec2_hourly"].get(instance_type, _RATE_TABLE["ec2_default_hourly"])
    return rate * HOURS_PER_MONTH


def _ebs_savings(instance_type_field: str) -> float:
    # instance_type_field for EBS rows looks like "gp3 100GB"
    try:
        vol_type, size_str = instance_type_field.split(" ")
        size_gb = float(size_str.replace("GB", ""))
    except (ValueError, AttributeError):
        return 0.0

    rate = _RATE_TABLE["ebs_per_gb_month"].get(vol_type, _RATE_TABLE["ebs_per_gb_month"]["gp2"])
    return rate * size_gb


def _eip_savings() -> float:
    return _RATE_TABLE["eip_unassociated_hourly"] * HOURS_PER_MONTH


def estimate_savings(rows: list[dict]) -> dict:
    breakdown = []
    total = 0.0

    for row in rows:
        rtype = row.get("resource_type", "")
        monthly = 0.0

        if "EC2 Instance (stopped)" in rtype:
            monthly = _ec2_stopped_savings(row.get("instance_type", ""))
        elif "EBS Volume" in rtype:
            monthly = _ebs_savings(row.get("instance_type", ""))
        elif "Elastic IP" in rtype:
            monthly = _eip_savings()
        else:
            # low-cpu "review for downsizing" rows aren't counted as
            # guaranteed savings, downsizing is a judgment call not a
            # straightforward removal
            continue

        breakdown.append({"resource_id": row.get("resource_id"), "type": rtype, "monthly_usd": round(monthly, 2)})
        total += monthly

    return {"total_monthly_usd": round(total, 2), "breakdown": breakdown}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="path to idle_detector CSV report")
    parser.add_argument("--current-monthly-bill", type=float, required=True)
    args = parser.parse_args()

    with open(args.csv) as f:
        rows = list(csv.DictReader(f))

    result = estimate_savings(rows)

    print(f"\nEstimated monthly savings from cleanup: ${result['total_monthly_usd']:.2f}")
    for item in result["breakdown"]:
        print(f"  - {item['type']}: {item['resource_id']} -> ${item['monthly_usd']:.2f}/mo")

    pct = (result["total_monthly_usd"] / args.current_monthly_bill) * 100
    print(f"\nAs % of current ${args.current_monthly_bill:.2f}/mo bill: {pct:.1f}%")
