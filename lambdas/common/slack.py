"""
Tiny wrapper around a Slack incoming webhook. Nothing fancy - both Lambdas
import this instead of each rolling their own requests.post call.
"""

import os
import json
import urllib.request

WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL")


def post_message(text: str, blocks: list | None = None) -> None:
    """Send a message to Slack. Swallows errors on purpose - a Slack outage
    shouldn't make the Lambda fail and retry-storm, the cost data / idle
    scan still gets written to S3/logs either way."""
    if not WEBHOOK_URL:
        print("SLACK_WEBHOOK_URL not set, skipping Slack post. Message was:")
        print(text)
        return

    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks

    req = urllib.request.Request(
        WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                print(f"slack post returned {resp.status}, body: {resp.read()}")
    except Exception as e:
        # logged, not raised - see docstring
        print(f"slack post failed: {e}")
