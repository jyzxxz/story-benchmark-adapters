"""Declared infrastructure capacity, independent of generation budgets."""
import json
from pathlib import Path

from story_benchmark.adapters.infiplot import InfiPlotError

DEFAULT_REQUEST_BODY_LIMIT_BYTES = 64 * 1024 * 1024


def request_body_limit(config):
    value = config.get("native_request_body_limit_bytes", DEFAULT_REQUEST_BODY_LIMIT_BYTES)
    if value is not None and (type(value) is not int or not 0 < value <= 2**53 - 1):
        raise InfiPlotError("invalid_request_body_limit", "native_request_body_limit_bytes must be a positive safe integer or null")
    return value


def verify_request_body_config(evidence_path, requested):
    try:
        evidence = json.loads(Path(evidence_path).read_text())
        loads = evidence["loads"]
        # One load is the launcher's own inspection. At least one further load
        # must have happened inside Next startup before readiness is accepted.
        valid = (evidence["requested_bytes"] == requested and len(loads) >= 2 and
                 all(row["effective_bytes"] == (row["before_bytes"] if requested is None else requested)
                     for row in loads))
    except (OSError, ValueError, KeyError, TypeError):
        valid = False
    if not valid:
        raise InfiPlotError("request_body_config_not_applied", "Native Next startup did not confirm the declared request-body capacity")
