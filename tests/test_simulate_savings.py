import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from simulate_savings import estimate_savings  # noqa: E402


def test_estimate_savings_basic():
    rows = [
        {"resource_type": "EC2 Instance (stopped)", "resource_id": "i-1", "instance_type": "t3.micro"},
        {"resource_type": "EBS Volume (unattached)", "resource_id": "vol-1", "instance_type": "gp3 100GB"},
        {"resource_type": "Elastic IP (unassociated)", "resource_id": "eip-1", "instance_type": "1.2.3.4"},
    ]
    result = estimate_savings(rows)
    assert result["total_monthly_usd"] > 0
    assert len(result["breakdown"]) == 3


def test_low_cpu_rows_excluded():
    rows = [
        {"resource_type": "EC2 Instance (low CPU - review)", "resource_id": "i-2", "instance_type": "m5.large"},
    ]
    result = estimate_savings(rows)
    assert result["total_monthly_usd"] == 0
    assert result["breakdown"] == []


def test_unknown_instance_type_falls_back():
    rows = [
        {"resource_type": "EC2 Instance (stopped)", "resource_id": "i-3", "instance_type": "totally.made.up"},
    ]
    result = estimate_savings(rows)
    assert result["total_monthly_usd"] > 0  # uses the default fallback rate
