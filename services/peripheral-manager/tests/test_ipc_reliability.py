"""Mock-only PERIPH-EXTRACT-01C: failures, deadlines and replay boundaries."""
from contextlib import redirect_stderr
import io
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_host_manager import h, cli, manager, req, SERVICE, SystemdFake


def request(action="status", revision=None):
    return req(action, str(uuid4()), revision)


def confirmed_args(operation="start"):
    return SimpleNamespace(socket="/fixture-only", robot_id=SERVICE["robot_id"],
                           peripheral_id=SERVICE["peripheral_id"], operation=operation,
                           confirm=f"{operation.upper()} {SERVICE['robot_id']}/{SERVICE['peripheral_id']}")


class TestFailureSemantics(unittest.TestCase):
    def test_subprocess_timeout_quarantines_even_if_target_observed(self):
        class LateEffect(SystemdFake):
            def __call__(self, argv, **kwargs):
                if argv[2] == "start":
                    self.calls.append(list(argv))
                    self.active = "active"
                    raise subprocess.TimeoutExpired(argv, h.SUBPROCESS_TIMEOUT_SEC)
                return super().__call__(argv, **kwargs)

        broker, fake = manager(LateEffect())
        with patch.object(h, "device_identity", return_value=("present", True)):
            initial = broker.process(request())
            command = request("acquisition.start", initial["status_revision"])
            result = broker.process(command)
            self.assertEqual(result["disposition"], "failed")
            self.assertEqual(result["error_code"], "systemd_outcome_unknown")
            self.assertEqual(result["publisher"]["lifecycle"], "running")
            self.assertGreater(result["status_revision"], initial["status_revision"])
            self.assertEqual(broker.process(command), result)
            status = broker.process(request())
            self.assertEqual(status["error_code"], "reconciliation_required")
            blocked = broker.process(request("acquisition.stop", status["status_revision"]))
            self.assertEqual(blocked["error_code"], "reconciliation_required")
        self.assertEqual(sum(call[2] == "start" for call in fake.calls), 1)
        self.assertFalse(any(call[2] == "stop" for call in fake.calls))

    def test_nonzero_or_unobserved_target_is_uncertain(self):
        for returncode in (0, 1):
            class Unconfirmed(SystemdFake):
                def __call__(self, argv, **kwargs):
                    if argv[2] == "start":
                        self.calls.append(list(argv))
                        return SimpleNamespace(returncode=returncode, stdout="", stderr="")
                    return super().__call__(argv, **kwargs)
            broker, fake = manager(Unconfirmed())
            with patch.object(h, "device_identity", return_value=("present", True)):
                status = broker.process(request())
                result = broker.process(request("acquisition.start", status["status_revision"]))
                self.assertEqual(result["error_code"], "systemd_outcome_unknown")
                self.assertEqual(broker.process(request())["error_code"], "reconciliation_required")

    def test_post_probe_error_keeps_quarantine(self):
        broker, fake = manager()
        with patch.object(h, "device_identity", return_value=("present", True)):
            status = broker.process(request())
            with patch.object(broker, "_status", side_effect=[status, OSError("probe lost")]):
                with self.assertRaises(OSError):
                    broker.process(request("acquisition.start", status["status_revision"]))
            self.assertEqual(broker.process(request())["error_code"], "reconciliation_required")
            self.assertEqual(sum(call[2] == "start" for call in fake.calls), 1)

    def test_changed_revision_or_uuid_case_cannot_reuse_id(self):
        broker, fake = manager()
        with patch.object(h, "device_identity", return_value=("present", True)):
            status = broker.process(request())
            command = request("acquisition.start", status["status_revision"])
            command["request_id"] = "a50c1501-798e-4a56-adf1-515469ac3444"
            result = broker.process(command)
            changed = dict(command, expected_status_revision=result["status_revision"])
            self.assertEqual(broker.process(changed)["error_code"], "request_id_reused")
            changed = dict(command, request_id=command["request_id"].upper())
            self.assertEqual(broker.process(changed)["error_code"], "request_id_reused")
            # Caller mutation cannot alter the cached acknowledgement.
            result["publisher"]["lifecycle"] = "unknown"
            self.assertEqual(broker.process(command)["publisher"]["lifecycle"], "running")
        self.assertEqual(sum(call[2] == "start" for call in fake.calls), 1)

    def test_consumed_revision_prevents_replay_after_ledger_eviction(self):
        broker, fake = manager()
        with patch.object(h, "MAX_HISTORY", 1), patch.object(h, "device_identity", return_value=("present", True)):
            status = broker.process(request())
            command = request("acquisition.start", status["status_revision"])
            running = broker.process(command)
            broker.process(request("acquisition.stop", running["status_revision"]))
            self.assertNotIn(command["request_id"], broker.history)
            self.assertEqual(broker.process(command)["error_code"], "stale_status_revision")
        self.assertEqual(sum(call[2] == "start" for call in fake.calls), 1)

    def test_revision_rejects_previous_epoch_with_distinct_random_seed(self):
        with patch.object(h.secrets, "randbelow", side_effect=[100, 200]), \
             patch.object(h, "device_identity", return_value=("present", True)):
            old, _ = manager()
            new, fake = manager()
            command = request("acquisition.start", old.process(request())["status_revision"])
            self.assertEqual(new.process(command)["error_code"], "stale_status_revision")
        self.assertFalse(any(call[2] == "start" for call in fake.calls))

    def test_known_lost_peer_before_dispatch_never_starts(self):
        broker, fake = manager()
        with patch.object(h, "device_identity", return_value=("present", True)):
            status = broker.process(request())
            response = broker.process(request("acquisition.start", status["status_revision"]),
                                      may_dispatch=lambda: False)
            self.assertEqual(response["error_code"], "client_disconnected")
        self.assertFalse(any(call[2] == "start" for call in fake.calls))

    def test_cli_does_not_retry_after_lost_action_reply(self):
        broker, fake = manager()
        calls = []
        def talk(_endpoint, message):
            calls.append(dict(message))
            answer = broker.process(message)
            if message["action"] != "status":
                raise TimeoutError("reply lost")
            return answer
        with patch.object(h, "device_identity", return_value=("present", True)), redirect_stderr(io.StringIO()) as stderr:
            code = cli.execute(confirmed_args(), talk)
        self.assertEqual(code, cli.EXIT_UNCONFIRMED)
        self.assertEqual([call["action"] for call in calls], ["status", "acquisition.start"])
        self.assertIn(calls[-1]["request_id"], stderr.getvalue())
        self.assertIn("OUTCOME_UNKNOWN", stderr.getvalue())
        self.assertEqual(fake.active, "active")

    def test_status_failure_or_missing_confirmation_does_not_dispatch(self):
        calls = []
        def unavailable(_endpoint, message):
            calls.append(message)
            raise TimeoutError()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.execute(confirmed_args(), unavailable), 1)
            args = confirmed_args()
            args.confirm = ""
            self.assertEqual(cli.execute(args, unavailable), 2)
        self.assertEqual([call["action"] for call in calls], ["status"])

    def test_malformed_reply_and_wrong_scope_never_count_as_success(self):
        broker, _ = manager()
        q = request()
        with patch.object(h, "device_identity", return_value=("present", True)):
            good = broker.process(q)
        self.assertEqual(cli.validate_reply(good, q), good)
        for change in (
            {"robot_id": "other-robot"}, {"peripheral_id": "other-imu"},
            {"request_id": str(uuid4())}, {"status_revision": True},
            {"status_revision": -1}, {"observed_at": "not-a-timestamp"},
            {"publisher": {}}, {"device": {"presence": "absent", "binding_verified": True}},
            {"unexpected": 1}, {"actuation_enabled": True}, {"disposition": "accepted"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                cli.validate_reply(dict(good, **change), q)

    def test_json_rejects_duplicate_nonfinite_and_invalid_encoding(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":{"a":1,"a":2}}',
                    b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{"x":"\xff"}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                h.strict_json(raw)

    def test_deadline_budget_not_reset_by_partial_frame(self):
        class Partial:
            def __init__(self):
                self.timeouts = []
            def settimeout(self, value):
                self.timeouts.append(value)
            def recv(self, size):
                return b"x"
        stream = Partial()
        with patch.object(h.time, "monotonic", side_effect=[1.0, 1.9, 2.1]):
            with self.assertRaises(TimeoutError):
                h.read_frame(stream, 2.0)
        self.assertEqual(len(stream.timeouts), 2)
        self.assertLess(stream.timeouts[1], stream.timeouts[0])
        self.assertGreater(h.CLIENT_TIMEOUT_SEC, 5 * h.SUBPROCESS_TIMEOUT_SEC + h.READ_TIMEOUT_SEC + h.WRITE_TIMEOUT_SEC)

    def test_frame_rejects_partial_oversize_and_pipelined_messages(self):
        for data in (b'{}', b'x' * h.MAX_MESSAGE, b'{}\n{}\n'):
            left, right = socket.socketpair()
            with left, right:
                right.sendall(data)
                right.shutdown(socket.SHUT_WR)
                with self.subTest(data_size=len(data)), self.assertRaises(ValueError):
                    h.read_frame(left, time.monotonic() + 1.0)

    def test_disconnected_client_isolated_and_next_socket_action_works(self):
        if not hasattr(socket, "SO_PEERCRED"):
            self.skipTest("Linux SO_PEERCRED required")
        broker, fake = manager()
        with TemporaryDirectory() as temp, patch.object(h, "device_identity", return_value=("present", True)):
            path = Path(temp) / "ipc" / "broker.sock"
            stop = threading.Event()
            worker = threading.Thread(target=h.serve, args=(broker, path),
                                      kwargs={"stop_event": stop, "register_handlers": False},
                                      daemon=True)
            worker.start()
            try:
                for _ in range(100):
                    if path.exists():
                        break
                    time.sleep(0.01)
                self.assertTrue(path.exists())
                initial = cli.transact(path, request())
                # Force a reply send error, then exercise actual next connection.
                original = h.send_frame
                server_dropped = threading.Event()
                def drop_one(conn, payload, deadline):
                    if not server_dropped.is_set() and b'"disposition":"status"' in payload:
                        server_dropped.set()
                        raise BrokenPipeError("simulated client disconnect")
                    return original(conn, payload, deadline)
                with patch.object(h, "send_frame", side_effect=drop_one):
                    with self.assertRaises((ValueError, OSError)):
                        cli.transact(path, request())
                self.assertTrue(server_dropped.is_set())
                self.assertTrue(worker.is_alive())
                running = cli.transact(path, request("acquisition.start", initial["status_revision"]))
                self.assertEqual(running["disposition"], "accepted")
                self.assertEqual(running["publisher"]["lifecycle"], "running")
            finally:
                stop.set()
                worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual(sum(call[2] == "start" for call in fake.calls), 1)


if __name__ == "__main__":
    unittest.main()
