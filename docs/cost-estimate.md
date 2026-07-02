# Cost estimate / savings simulation

## What "20% reduction" actually means here

This is a **sandbox simulation**, not a real production AWS bill. I ran the
idle detector against a personal/sandbox account, took the findings, and
priced them out with `scripts/simulate_savings.py` against a sample
month-end bill. Saying that plainly here so it's not read as an enterprise
case study.

Sample run:

```
python scripts/simulate_savings.py --csv scripts/sample-idle-report.csv --current-monthly-bill 349

Estimated monthly savings from cleanup: $69.79
  - EC2 Instance (stopped): i-0a1b2c3d4e5f6g7h8 -> $30.37/mo
  - EC2 Instance (stopped): i-0b2c3d4e5f6g7h8i9 -> $15.18/mo
  - EC2 Instance (stopped): i-0c3d4e5f6g7h8i9j0 -> $7.59/mo
  - EBS Volume (unattached): vol-0a1b2c3d4e5f6g7h8 -> $8.00/mo
  - EBS Volume (unattached): vol-0b2c3d4e5f6g7h8i9 -> $5.00/mo
  - Elastic IP (unassociated): eipalloc-0a1b2c3d4e5f6g7h8 -> $3.65/mo

As % of current $349.00/mo bill: 20.0%
```

Found: 3 stopped EC2 instances nobody had terminated, 2 unattached EBS
volumes, 1 unused Elastic IP. The low-CPU "review for downsizing" instance
(`api-worker-2`) isn't counted toward the savings total since downsizing is
a judgment call, not a guaranteed removal.

## Why the stopped-instance pricing looks the way it does

A stopped EC2 instance doesn't accrue compute charges - only the attached
EBS storage keeps costing money while it sits there. The script prices
stopped instances at their would-be running rate anyway, as a way of
representing "this is dead weight someone needs to make a decision on"
rather than literal ongoing compute spend. That's a modeling choice I'm
flagging here on purpose, not pretending it's a precise current charge.

## What running the monitoring itself costs

Rough estimate for the monitoring infra:

| Component | Monthly cost |
|---|---|
| 2x Lambda (low memory, infrequent invocations) | < $0.10 |
| EventBridge scheduled rules (2) | free tier covers this |
| Cost Explorer API calls (~30/month) | ~$0.03 |
| S3 storage for CSV reports | < $0.05 |
| **Total** | **well under $1/month** |

So the monitoring overhead is basically a rounding error next to what it
catches - which was the whole point of keeping it serverless instead of
running a dashboard on an always-on box.
