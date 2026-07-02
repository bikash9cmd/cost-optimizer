"""
Scans the account for resources that are probably just sitting there
costing money: EC2 instances stopped for a while, unattached EBS volumes,
unassociated Elastic IPs, and running instances with suspiciously low
CPU usage (candidates for downsizing, not necessarily idle, so these get
flagged separately and don't count toward the "idle" total).

Runs on a weekly EventBridge schedule - idle resources don't really change
hour to hour so daily would just be repeating the same findings.
"""

import os
import csv
import io
import boto3
from datetime import datetime, timezone, timedelta

from common.slack import post_message

ec2 = boto3.client("ec2")
cloudwatch = boto3.client("cloudwatch")
s3 = boto3.client("s3")

REPORT_BUCKET = os.environ.get("REPORT_BUCKET")
STOPPED_DAYS_THRESHOLD = int(os.environ.get("STOPPED_DAYS_THRESHOLD", "7"))
LOW_CPU_THRESHOLD_PCT = float(os.environ.get("LOW_CPU_THRESHOLD_PCT", "5"))


def find_stopped_instances() -> list[dict]:
    """Instances that have been in 'stopped' state for longer than the
    threshold. EC2 doesn't expose 'how long has this been stopped' directly,
    so we use the state transition reason timestamp, which is a bit hacky
    but it's the only timestamp the API actually gives us for this."""
    findings = []
    paginator = ec2.get_paginator("describe_instances")

    for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                transition_reason = instance.get("StateTransitionReason", "")
                stopped_since = _parse_transition_time(transition_reason)

                if stopped_since is None:
                    continue

                days_stopped = (datetime.now(timezone.utc) - stopped_since).days
                if days_stopped >= STOPPED_DAYS_THRESHOLD:
                    findings.append({
                        "resource_type": "EC2 Instance (stopped)",
                        "resource_id": instance["InstanceId"],
                        "instance_type": instance.get("InstanceType", "unknown"),
                        "days_idle": days_stopped,
                        "name": _get_name_tag(instance.get("Tags", [])),
                    })

    return findings


def _parse_transition_time(reason: str):
    # reason looks like: "User initiated (2025-11-02 14:33:01 GMT)"
    if "(" not in reason or ")" not in reason:
        return None
    try:
        ts_str = reason.split("(")[1].split(")")[0].replace(" GMT", "")
        return datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def find_unattached_volumes() -> list[dict]:
    findings = []
    paginator = ec2.get_paginator("describe_volumes")

    for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
        for volume in page["Volumes"]:
            findings.append({
                "resource_type": "EBS Volume (unattached)",
                "resource_id": volume["VolumeId"],
                "instance_type": f"{volume['VolumeType']} {volume['Size']}GB",
                "days_idle": (datetime.now(timezone.utc) - volume["CreateTime"]).days,
                "name": _get_name_tag(volume.get("Tags", [])),
            })

    return findings


def find_unassociated_eips() -> list[dict]:
    findings = []
    resp = ec2.describe_addresses()

    for address in resp["Addresses"]:
        if "AssociationId" not in address:
            findings.append({
                "resource_type": "Elastic IP (unassociated)",
                "resource_id": address.get("AllocationId", address.get("PublicIp")),
                "instance_type": address.get("PublicIp", "unknown"),
                "days_idle": None,  # EIPs don't carry a creation timestamp
                "name": _get_name_tag(address.get("Tags", [])),
            })

    return findings


def find_low_cpu_instances() -> list[dict]:
    """Running instances averaging under LOW_CPU_THRESHOLD_PCT over the last
    7 days. These get flagged as 'review for downsizing', not 'idle' -
    something running at 4% CPU might still be doing useful work on a
    smaller instance type, it's not free money like a stopped box is."""
    findings = []
    paginator = ec2.get_paginator("describe_instances")

    for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["running"]}]):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                avg_cpu = _get_avg_cpu(instance["InstanceId"])
                if avg_cpu is not None and avg_cpu < LOW_CPU_THRESHOLD_PCT:
                    findings.append({
                        "resource_type": "EC2 Instance (low CPU - review)",
                        "resource_id": instance["InstanceId"],
                        "instance_type": instance.get("InstanceType", "unknown"),
                        "days_idle": None,
                        "name": _get_name_tag(instance.get("Tags", [])),
                        "avg_cpu_pct": round(avg_cpu, 1),
                    })

    return findings


def _get_avg_cpu(instance_id: str):
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)

    resp = cloudwatch.get_metric_statistics(
        Namespace="AWS/EC2",
        MetricName="CPUUtilization",
        Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
        StartTime=start,
        EndTime=end,
        Period=3600,
        Statistics=["Average"],
    )

    datapoints = resp.get("Datapoints", [])
    if not datapoints:
        return None  # no data, don't flag it either way

    return sum(d["Average"] for d in datapoints) / len(datapoints)


def _get_name_tag(tags: list) -> str:
    for tag in tags:
        if tag.get("Key") == "Name":
            return tag.get("Value", "")
    return "(no name tag)"


def _write_csv_report(all_findings: list[dict]) -> str:
    if not REPORT_BUCKET:
        return ""

    buffer = io.StringIO()
    fieldnames = ["resource_type", "resource_id", "name", "instance_type", "days_idle", "avg_cpu_pct"]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(all_findings)

    key = f"idle-reports/{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv"
    s3.put_object(Bucket=REPORT_BUCKET, Key=key, Body=buffer.getvalue())
    return f"s3://{REPORT_BUCKET}/{key}"


def _build_summary_message(findings: dict, report_path: str) -> str:
    stopped = len(findings["stopped_instances"])
    volumes = len(findings["unattached_volumes"])
    eips = len(findings["unassociated_eips"])
    low_cpu = len(findings["low_cpu_instances"])
    total_idle = stopped + volumes + eips

    if total_idle == 0 and low_cpu == 0:
        return ":white_check_mark: Idle resource scan complete - nothing to flag this week."

    lines = [":mag: *Weekly idle resource scan*"]
    if stopped:
        lines.append(f"• {stopped} stopped EC2 instance(s) idle > {STOPPED_DAYS_THRESHOLD} days")
    if volumes:
        lines.append(f"• {volumes} unattached EBS volume(s)")
    if eips:
        lines.append(f"• {eips} unassociated Elastic IP(s)")
    if low_cpu:
        lines.append(f"• {low_cpu} running instance(s) under {LOW_CPU_THRESHOLD_PCT}% avg CPU (review for downsizing)")

    if report_path:
        lines.append(f"\nFull report: `{report_path}`")

    return "\n".join(lines)


def handler(event, context):
    findings = {
        "stopped_instances": find_stopped_instances(),
        "unattached_volumes": find_unattached_volumes(),
        "unassociated_eips": find_unassociated_eips(),
        "low_cpu_instances": find_low_cpu_instances(),
    }

    all_rows = (
        findings["stopped_instances"]
        + findings["unattached_volumes"]
        + findings["unassociated_eips"]
        + findings["low_cpu_instances"]
    )

    report_path = _write_csv_report(all_rows)
    message = _build_summary_message(findings, report_path)
    post_message(message)

    return {
        "stopped_instances": len(findings["stopped_instances"]),
        "unattached_volumes": len(findings["unattached_volumes"]),
        "unassociated_eips": len(findings["unassociated_eips"]),
        "low_cpu_instances": len(findings["low_cpu_instances"]),
        "report_path": report_path,
    }
