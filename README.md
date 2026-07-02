# AWS Cloud Cost Optimisation & Monitoring Dashboard

A small serverless setup that watches AWS spend and flags resources that
are quietly costing money while doing nothing. Started this after noticing
a personal/sandbox AWS bill that had a handful of EBS volumes attached to
nothing and an Elastic IP nobody was using.

## What it does

- **Spend alerting** — a Lambda runs on a schedule, pulls cost data from
  Cost Explorer, and posts to Slack if spend crosses a threshold (daily
  run-rate and a month-to-date check, both configurable).
- **Idle resource detection** — a separate Lambda scans for the usual
  silent-money-leak suspects: stopped EC2 instances that have been stopped
  too long, unattached EBS volumes, unassociated Elastic IPs, and
  CloudWatch-quiet (low CPU) running instances. Posts a summary to Slack
  and dumps a CSV report to S3.
- Both run off EventBridge schedules, no servers to babysit.

## Why I built it this way

I didn't want a dashboard that needs a server running 24/7 just to check a
number a couple times a day — Lambda + EventBridge schedule covers it for
basically nothing in cost, which felt right for a *cost optimisation* tool
(would be a bit ironic to run an expensive always-on box just to tell you
you're spending too much elsewhere).

Cost Explorer's API itself isn't free per-call, so the spend-check Lambda
is on purpose not running every 5 minutes — daily is enough resolution for
catching a runaway bill before it gets out of hand, see `docs/runbook.md`.

## Layout

```
lambdas/
  spend_alert/        -> pulls Cost Explorer data, posts to Slack on threshold breach
  idle_detector/       -> scans EC2/EBS/EIP for idle resources, posts summary + writes CSV
  common/              -> shared Slack + AWS client helpers

infra/
  template.yaml         -> SAM template wiring the two Lambdas + EventBridge rules + IAM

scripts/
  simulate_savings.py   -> the "20% reduction" estimate - reads idle_detector's
                            output and prices out what those resources actually cost

docs/
  runbook.md             -> day-2 operational stuff: how to rotate the Slack
                            webhook, what to do when an alert fires, how to
                            tune thresholds
  architecture.md        -> how the pieces talk to each other
  cost-estimate.md        -> roughly what running this costs vs. what it saved

tests/
  test_idle_detector.py
```

## Running tests

```bash
pip install pytest boto3
AWS_DEFAULT_REGION=us-east-1 pytest tests/ -v
```

(The region env var is only needed because `boto3.client()` at import time
wants one - same as it would when actually running in Lambda, where the
runtime sets this automatically.)

## Setup

```bash
pip install -r requirements.txt -t lambdas/common/  # boto3 etc bundled for local testing
sam build
sam deploy --guided
```

You'll need a Slack incoming webhook URL — set it as `SLACK_WEBHOOK_URL` in
`infra/template.yaml`'s parameters (or in Secrets Manager if you don't want
it sitting in plaintext, see the note in `docs/runbook.md`, that's actually
what I do for anything beyond personal testing).

## Result

Ran the idle detector against a sandbox account that had been accumulating
cruft for a few months. Found 3 stopped EC2 instances nobody had terminated,
2 unattached EBS volumes, and 1 unused Elastic IP. Pricing those out against
their actual monthly cost (`scripts/simulate_savings.py`) came out to
roughly a 20% reduction against that account's monthly bill — numbers are in
`docs/cost-estimate.md`. This is a simulated/sandbox number, not a
production AWS bill, said plainly so nobody mistakes it for an enterprise
case study.

## What's not done

- [ ] No actual dashboard UI yet — "dashboard" right now is the Slack
      messages + the CSV in S3. Was planning to wire the CSV up to
      QuickSight but ran out of time before the module deadline.
- [ ] Idle detection thresholds (e.g. "stopped > 7 days") are hardcoded,
      should be Parameter Store values so they're tunable without a redeploy.
- [ ] Only handles EC2 / EBS / EIP — RDS idle detection (low connections)
      would be the obvious next thing to add.
