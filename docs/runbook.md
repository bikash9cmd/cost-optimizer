# Runbook

Operational notes - mostly written for future me, in case I haven't touched
this in 3 months and forget how it's wired.

## When a spend alert fires

1. Check the Slack message for whether it's the month-to-date budget or the
   daily threshold that tripped.
2. Go to Cost Explorer in the console, filter by service, sort by cost, to
   see what's actually driving it (the Lambda only gives totals, not a
   breakdown - intentionally kept the Cost Explorer query simple/cheap,
   see the note in `docs/architecture.md` about API costs).
3. Common culprits I've actually seen: a forgotten NAT Gateway running 24/7
   (these are sneaky expensive for what they do), a data transfer spike,
   or just normal growth that means the threshold needs raising.
4. If it's a real problem, fix it. If it's just growth, update
   `MonthlyBudgetUsd` / `DailyThresholdUsd` in the SAM template parameters
   and redeploy.

## When the idle scan flags something

1. Read the CSV from S3 (path is in the Slack message) for the full list,
   Slack only shows counts.
2. For stopped EC2 instances: check if anyone's actually planning to
   restart it. If not, terminate it (and any attached EBS volume that isn't
   needed).
3. For unattached EBS volumes: snapshot first if there's any chance the
   data matters, then delete. Don't skip the snapshot step - learned this
   one the hard way on a personal project, not this one specifically, but
   close enough that I'm paranoid about it now.
4. For unassociated Elastic IPs: release them unless there's a real reason
   to keep the IP reserved (e.g. a planned cutover).
5. For low-CPU "review for downsizing" entries: these are NOT auto-flagged
   as something to delete. Look at the workload before downsizing -
   bursty/scheduled jobs can look idle on a CPU average and still need
   the headroom.

## Rotating the Slack webhook

If the webhook URL leaks or just needs rotating:
1. Create a new incoming webhook in the Slack app config.
2. Update the `SlackWebhookUrl` parameter and redeploy
   (`sam deploy --parameter-overrides SlackWebhookUrl=<new-url>`).
3. Revoke the old webhook in Slack.

For anything beyond personal/sandbox use, move the webhook URL into Secrets
Manager and have both Lambdas fetch it at cold start instead of passing it
as a plaintext environment variable - didn't do this for the initial build
since it's a personal account, flagged here so it's not forgotten before
this touches anything that matters.

## Tuning thresholds

All the knobs (`MonthlyBudgetUsd`, `DailyThresholdUsd`,
`StoppedDaysThreshold`, `LowCpuThresholdPct`) live in
`infra/template.yaml` as SAM parameters. No Parameter Store wiring yet (see
README's "what's not done" list) so changing these means a redeploy, not
just a console edit - fine for now at this scale, annoying if this grows
into something other people are also adjusting.

`MonthlyBudgetUsd` and `DailyThresholdUsd` must be greater than 0. The
template rejects anything lower at deploy time, and if the Lambda env vars
are edited by hand to `0`, a negative number or something like `$100`, the
spend-alert function fails on cold start with an error naming the bad
variable (check its CloudWatch logs) rather than silently skipping alerts.

## Failure modes I've actually hit

- Cost Explorer occasionally returns a `DataUnavailableException` for the
  current day if you query too early in the day, before AWS has finished
  aggregating - the daily 07:00 UTC schedule was picked partly to dodge this,
  data's reliably there by then.
- CloudWatch `GetMetricStatistics` returns an empty datapoints list for
  instances that were only running part of the lookback window - handled by
  just skipping those instances rather than treating no-data as low-CPU.
