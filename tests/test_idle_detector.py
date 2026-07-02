"""
Tests for the pure-logic helper functions in idle_detector - the parts that
don't need a live AWS connection. Not testing find_stopped_instances() etc.
directly since those need boto3 clients / moto mocking, which felt like
overkill for a module project - these cover the bits most likely to have
an actual bug (the transition-time parsing especially, that format string
took a couple tries to get right).
"""

import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambdas"))

from idle_detector.handler import _parse_transition_time, _get_name_tag  # noqa: E402


def test_parse_transition_time_valid():
    reason = "User initiated (2025-11-02 14:33:01 GMT)"
    result = _parse_transition_time(reason)
    assert result == datetime(2025, 11, 2, 14, 33, 1, tzinfo=timezone.utc)


def test_parse_transition_time_missing_parens():
    assert _parse_transition_time("no timestamp here") is None


def test_parse_transition_time_garbage():
    assert _parse_transition_time("Server.InternalError (not-a-date)") is None


def test_get_name_tag_found():
    tags = [{"Key": "Environment", "Value": "prod"}, {"Key": "Name", "Value": "my-box"}]
    assert _get_name_tag(tags) == "my-box"


def test_get_name_tag_missing():
    tags = [{"Key": "Environment", "Value": "prod"}]
    assert _get_name_tag(tags) == "(no name tag)"


def test_get_name_tag_empty_list():
    assert _get_name_tag([]) == "(no name tag)"
