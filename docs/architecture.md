# Architecture

```
                     EventBridge (cron, daily 07:00 UTC)
                              |
                              v
                    Lambda: spend_alert
                    - Cost Explorer GetCostAndUsage
                    - compares vs MONTHLY_BUDGET_USD / DAILY_THRESHOLD_USD
                              |
                              v
                       Slack webhook (only posts if over threshold)


                     EventBridge (cron, weekly Mon 08:00 UTC)
                              |
                              v
                    Lambda: idle_detector
                    - EC2 DescribeInstances (stopped + running)
                    - EC2 DescribeVolumes (unattached)
                    - EC2 DescribeAddresses (unassociated EIPs)
                    - CloudWatch GetMetricStatistics (avg CPU, 7-day window)
                              |
                    +---------+---------+
                    |                   |
                    v                   v
              S3 (CSV report)     Slack webhook (summary)
```

## Why two separate Lambdas instead of one

Different schedules (daily vs weekly), different IAM permissions (Cost
Explorer vs EC2/CloudWatch describe calls), and different blast radius if
one breaks. Splitting them means a bug in the idle scan doesn't take down
spend alerting, which felt like the more important one to keep reliable.

## IAM

Both functions use scoped-down inline policies in `infra/template.yaml` -
read-only `Describe*`/`Get*` calls plus S3 write access limited to the
report bucket. Neither Lambda can modify or terminate anything; this is
intentionally read-only/detection, not auto-remediation. Wiring up actual
auto-termination of idle resources felt like the wrong default for
something running unattended - false positives (an EIP held for a planned
launch, a stopped instance someone's coming back to next week) would be
expensive in a different way. Slack + a human decision felt like the right
level of automation here.

## Why CSV in S3 instead of a real dashboard

Originally planned to wire the weekly CSV into QuickSight for an actual
dashboard view, but ran out of runway before the module deadline. S3 + the
Slack summary covers "did anything idle show up" without the dashboard
infra; QuickSight is the obvious next step if this needs to be more than a
personal/team tool. Noted as unfinished in the README rather than pretending
it's there.

## Local testing without deploying

`sam local invoke` works for both functions if you've got Docker running
and valid AWS credentials exported - Cost Explorer and EC2 describe calls
both hit real AWS even when testing locally (no good way to mock Cost
Explorer convincingly), so this still needs real (read-only) AWS access to
test against, not just SAM tooling.
