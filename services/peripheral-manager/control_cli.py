#!/usr/bin/env python3
"""Local, same-user Peripheral Host Manager CLI (not a Console/browser API).

status: read-only. start/stop: explicit typed confirmation, bounded IPC,
and compare-and-swap status revision. Neither CLI nor broker opens hardware,
reads publisher tokens, touches CAN, or performs actuator operations.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import re
import socket
import sys
import time
from uuid import uuid4

from host_broker import (CLIENT_TIMEOUT_SEC, MAX_MESSAGE, RESULT_SCHEMA, SCHEMA,
                         SAFE_ID, SAFE_UNIT, peer_allowed, read_frame, remaining,
                         send_frame, socket_path, strict_json)


EXIT_UNCONFIRMED = 3
IPC_ERRORS = (OSError, ValueError, KeyError, RecursionError)
LIFECYCLES = {"not_installed", "stopped", "starting", "running", "stopping",
              "failed", "busy", "unknown"}


def validate_reply(response, request):
    """Strict local response shape/identity checks; full conformance is 01D."""
    def require(condition):
        if not condition:
            raise ValueError("invalid_or_untrusted_host_broker_reply")

    require(isinstance(response, dict))
    require(set(response) == {"schema_version", "request_id", "robot_id",
                              "peripheral_id", "observed_at", "device", "publisher",
                              "status_revision", "disposition", "error_code",
                              "actuation_enabled"})
    require(response["schema_version"] == RESULT_SCHEMA)
    require(all(response[k] == request[k] for k in
                ("request_id", "robot_id", "peripheral_id")))
    require(response["actuation_enabled"] is False)
    require(type(response["status_revision"]) is int
            and 0 <= response["status_revision"] < (1 << 53))
    observed = response["observed_at"]
    require(isinstance(observed, str))
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", observed))
    require(datetime.fromisoformat(observed.replace("Z", "+00:00")).utcoffset() is not None)
    disposition = response["disposition"]
    require(isinstance(disposition, str))
    require(disposition in (("status", "rejected") if request["action"] == "status"
                            else ("accepted", "no_op", "rejected", "failed")))
    error = response["error_code"]
    require(error is None or (isinstance(error, str)
                              and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", error)))
    if disposition in ("accepted", "no_op"):
        require(error is None)
        require(response["status_revision"] >= request["expected_status_revision"])
    if disposition in ("rejected", "failed"):
        require(error is not None)
    device = response["device"]
    require(isinstance(device, dict) and set(device) == {"presence", "binding_verified"})
    require(device["presence"] in ("present", "absent", "unknown")
            and type(device["binding_verified"]) is bool)
    require(not device["binding_verified"] or device["presence"] == "present")
    publisher = response["publisher"]
    require(isinstance(publisher, dict) and set(publisher) == {
        "lifecycle", "manager_available", "enabled_on_boot", "service_ref"})
    require(isinstance(publisher["lifecycle"], str) and publisher["lifecycle"] in LIFECYCLES)
    require(type(publisher["manager_available"]) is bool)
    require(publisher["enabled_on_boot"] is None or type(publisher["enabled_on_boot"]) is bool)
    service = publisher["service_ref"]
    require(service is None or (isinstance(service, str) and SAFE_UNIT.fullmatch(service)))
    if disposition in ("status", "accepted", "no_op"):
        require(service is not None)
    if disposition in ("accepted", "no_op"):
        target = "running" if request["action"] == "acquisition.start" else "stopped"
        require(publisher["manager_available"] and publisher["lifecycle"] == target)
    return response


def transact(endpoint, request: dict) -> dict:
    payload = json.dumps(request, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
    if len(payload) > MAX_MESSAGE:
        raise ValueError("request_exceeds_limit")
    deadline = time.monotonic() + CLIENT_TIMEOUT_SEC
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(remaining(deadline))
        stream.connect(str(endpoint))
        if not peer_allowed(stream):
            raise ValueError("untrusted_broker_peer")
        send_frame(stream, payload, deadline)
        raw = read_frame(stream, deadline)
    return validate_reply(strict_json(raw), request)


def execute(args, talk=transact) -> int:
    endpoint = args.socket or socket_path()
    if not SAFE_ID.fullmatch(args.robot_id) or not SAFE_ID.fullmatch(args.peripheral_id):
        print("Invalid peripheral identity; no request sent", file=sys.stderr)
        return 2
    base = {
        "schema_version": SCHEMA,
        "robot_id": args.robot_id,
        "peripheral_id": args.peripheral_id,
        "request_id": str(uuid4()),
        "action": "status",
    }
    if args.operation != "status":
        operator_phrase = f"{args.operation.upper()} {args.robot_id}/{args.peripheral_id}"
        if args.confirm != operator_phrase:
            print(f"Explicit operator confirmation required: --confirm '{operator_phrase}'",
                  file=sys.stderr)
            return 2
    try:
        status = validate_reply(talk(endpoint, base), base)
    except IPC_ERRORS:
        print("Status unavailable; no acquisition request sent", file=sys.stderr)
        return 1
    if args.operation == "status":
        print(json.dumps(status, indent=2))
        return 0 if status["disposition"] == "status" and status["error_code"] is None else 1

    if status["disposition"] != "status" or status["error_code"] is not None:
        print("Broker status not actionable; no acquisition request sent; reconcile",
              file=sys.stderr)
        return 1
    request = dict(base,
        request_id=str(uuid4()),
        action=f"acquisition.{args.operation}",
        expected_status_revision=status["status_revision"])
    try:
        response = validate_reply(talk(endpoint, request), request)
    except IPC_ERRORS:
        print(f"OUTCOME_UNKNOWN request_id={request['request_id']}; "
              "reconcile service and pending jobs; do not retry automatically",
              file=sys.stderr)
        return EXIT_UNCONFIRMED
    print(json.dumps(response, indent=2))
    if response["disposition"] == "failed" or response["error_code"] == "reconciliation_required":
        print(f"OUTCOME_UNKNOWN request_id={request['request_id']}; "
              "reconcile service and pending jobs; do not retry automatically",
              file=sys.stderr)
        return EXIT_UNCONFIRMED
    return 0 if response["disposition"] in ("accepted", "no_op") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["status", "start", "stop"])
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--peripheral-id", required=True)
    parser.add_argument("--socket", type=str, default=None)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    return execute(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except IPC_ERRORS as error:
        print(f"PERIPH-0.2 client: {type(error).__name__}; outcome unconfirmed; reconcile status before any retry",
              file=sys.stderr)
        sys.exit(1)
