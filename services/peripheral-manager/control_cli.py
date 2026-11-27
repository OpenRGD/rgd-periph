#!/usr/bin/env python3
"""Local, same-user Peripheral Host Manager CLI (not a Console/browser API).

status: read-only. start/stop: explicit typed confirmation, bounded IPC,
and compare-and-swap status revision. Neither CLI nor broker opens hardware,
reads publisher tokens, touches CAN, or performs actuator operations.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from uuid import uuid4

from host_broker import MAX_MESSAGE, RESULT_SCHEMA, SCHEMA, socket_path


def transact(endpoint, request: dict) -> dict:
    payload = json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(payload) > MAX_MESSAGE:
        raise ValueError("request_exceeds_limit")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(3)
        stream.connect(str(endpoint))
        stream.sendall(payload)
        raw = stream.makefile("rb").readline(MAX_MESSAGE + 1)
    if not raw or len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
        raise ValueError("invalid_host_broker_reply")
    response = json.loads(raw)
    if (not isinstance(response, dict)
        or response.get("schema_version") != RESULT_SCHEMA
        or response.get("request_id") != request["request_id"]
        or response.get("actuation_enabled") is not False):
        raise ValueError("invalid_or_untrusted_host_broker_reply")
    return response


def execute(args, talk=transact) -> int:
    endpoint = args.socket or socket_path()
    base = {
        "schema_version": SCHEMA,
        "robot_id": args.robot_id,
        "peripheral_id": args.peripheral_id,
        "request_id": str(uuid4()),
        "action": "status",
    }
    status = talk(endpoint, base)
    if args.operation == "status":
        print(json.dumps(status, indent=2))
        return 0 if status.get("disposition") == "status" else 1

    if status.get("disposition") != "status":
        print("Cannot confirm broker status; no acquisition action attempted", file=sys.stderr)
        return 1
    operator_phrase = f"{args.operation.upper()} {args.robot_id}/{args.peripheral_id}"
    if args.confirm != operator_phrase:
        print(f"Explicit operator confirmation required: --confirm '{operator_phrase}'",
              file=sys.stderr)
        return 2
    request = dict(base,
        request_id=str(uuid4()),
        action=f"acquisition.{args.operation}",
        expected_status_revision=status["status_revision"])
    response = talk(endpoint, request)
    print(json.dumps(response, indent=2))
    return 0 if response.get("disposition") in ("accepted", "no_op") else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["status", "start", "stop"])
    parser.add_argument("--robot-id", default="paky-01")
    parser.add_argument("--peripheral-id", default="torso-imu-01")
    parser.add_argument("--socket", type=str, default=None)
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    return execute(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, socket.timeout) as error:
        print(f"PERIPH-0.2 client: {type(error).__name__}; no command accepted",
              file=sys.stderr)
        sys.exit(1)
