"""Durable recovery: fake commands and isolated temporary directories only."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4
import json
import subprocess
import unittest

from test_host_manager import h, SERVICE, SystemdFake, req
from recovery_state import RecoveryError, RecoveryStore, EPOCH_STRIDE

CONFIG = {"schema_version": h.ALLOWLIST_SCHEMA, "services": [SERVICE]}
KEY = (SERVICE["robot_id"], SERVICE["peripheral_id"])


def command(revision, action="acquisition.start"):
    return req(action, str(uuid4()), revision)


class DurableRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state"
        RecoveryStore.initialize(self.path, CONFIG)
        self.probe = patch.object(h, "device_identity", return_value=("present", True))
        self.probe.start()
        self.addCleanup(self.probe.stop)

    def test_success_persisted_before_dispatch_then_new_epoch_rejects_old_command(self):
        fake = SystemdFake()
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            status = broker.process(req())
            request = command(status["status_revision"])
            original_run = h.run_unit
            def assert_intent(action, unit, runner):
                journal = json.loads((self.path / "recovery.json").read_text())
                self.assertEqual(journal["payload"]["pending"]["/".join(KEY)], request)
                return original_run(action, unit, runner)
            with patch.object(h, "run_unit", side_effect=assert_intent):
                result = broker.process(request)
            self.assertEqual(result["disposition"], "accepted")
            self.assertEqual(store.inspect()["pending"], {})
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            status = broker.process(req())
            self.assertGreaterEqual(status["status_revision"], 2 * EPOCH_STRIDE)
            self.assertEqual(broker.process(request)["error_code"], "stale_status_revision")
        self.assertEqual(sum(c[2] == "start" for c in fake.calls), 1)

    def test_crash_after_intent_before_dispatch_is_quarantined_on_restart(self):
        fake = SystemdFake()
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            request = command(broker.process(req())["status_revision"])
            store.begin(KEY, request)
            # Simulate death before the command; the next broker must not guess.
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            status = broker.process(req())
            self.assertEqual(status["error_code"], "reconciliation_required")
            self.assertEqual(broker.process(command(status["status_revision"]))["error_code"],
                             "reconciliation_required")
        self.assertFalse(any(c[2] == "start" for c in fake.calls))

    def test_crash_after_external_effect_keeps_pending_and_requires_offline_review(self):
        class TimeoutAfterEffect(SystemdFake):
            def __call__(self, argv, **kwargs):
                if argv[2] == "start":
                    self.calls.append(list(argv))
                    self.active = "active"
                    raise subprocess.TimeoutExpired(argv, 4)
                return super().__call__(argv, **kwargs)
        fake = TimeoutAfterEffect()
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            request = command(broker.process(req())["status_revision"])
            self.assertEqual(broker.process(request)["error_code"], "systemd_outcome_unknown")
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            status = broker.process(req())
            self.assertEqual(status["publisher"]["lifecycle"], "running")
            self.assertEqual(status["error_code"], "reconciliation_required")
            self.assertEqual(broker.process(command(status["status_revision"], "acquisition.stop"))["error_code"],
                             "reconciliation_required")
            with self.assertRaises(OSError):
                RecoveryStore(self.path, CONFIG)
        with RecoveryStore(self.path, CONFIG) as store:
            with self.assertRaises(RecoveryError):
                store.acknowledge(KEY, request["request_id"], "fixture-review", "wrong")
            with self.assertRaises(RecoveryError):
                store.acknowledge(KEY, str(uuid4()), "fixture-review", "wrong")
            phrase = f"ACK RECONCILED {'/'.join(KEY)} {request['request_id']}"
            store.acknowledge(KEY, request["request_id"], "fixture-review", phrase)
            self.assertEqual(store.inspect()["pending"], {})
            self.assertEqual(store.inspect()["last_ack"]["/".join(KEY)]["kind"],
                             "external_review_asserted")
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            self.assertIsNone(broker.process(req())["error_code"])
        self.assertEqual(sum(c[2] == "start" for c in fake.calls), 1)
        self.assertFalse(any(c[2] == "stop" for c in fake.calls))

    def test_write_failure_before_dispatch_blocks_all_later_actions(self):
        fake = SystemdFake()
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            status = broker.process(req())
            with patch.object(store, "_atomic", side_effect=OSError("fsync failed")):
                with self.assertRaises(OSError):
                    broker.process(command(status["status_revision"]))
            self.assertFalse(store.healthy)
            status = broker.process(req())
            self.assertEqual(status["error_code"], "recovery_unavailable")
            self.assertEqual(broker.process(command(status["status_revision"]))["error_code"],
                             "recovery_unavailable")
        self.assertFalse(any(c[2] == "start" for c in fake.calls))

    def test_completion_write_failure_never_reports_accepted_and_pending_survives(self):
        fake = SystemdFake()
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            request = command(broker.process(req())["status_revision"])
            original = store._atomic
            def fail_completion(fd, state):
                if not state["pending"]:
                    raise OSError("completion write failed")
                return original(fd, state)
            with patch.object(store, "_atomic", side_effect=fail_completion):
                with self.assertRaises(OSError):
                    broker.process(request)
            self.assertFalse(store.healthy)
        with RecoveryStore(self.path, CONFIG) as store:
            broker = h.Broker(CONFIG, fake, recovery=store)
            self.assertEqual(broker.process(req())["error_code"], "reconciliation_required")
        self.assertEqual(sum(c[2] == "start" for c in fake.calls), 1)

    def test_bad_state_missing_state_wrong_permissions_and_config_drift_fail_closed(self):
        journal = self.path / "recovery.json"
        original = journal.read_bytes()
        for data in (b"{}", b'{"payload":{},"sha256":"bad"}', original[:-3]):
            journal.write_bytes(data)
            with self.assertRaises(RecoveryError):
                RecoveryStore(self.path, CONFIG)
        journal.write_bytes(original)
        journal.chmod(0o644)
        with self.assertRaises(RecoveryError):
            RecoveryStore(self.path, CONFIG)
        journal.chmod(0o600)
        changed = deepcopy(CONFIG)
        changed["services"][0]["resource_lock"] = "fixture:other-lock"
        with self.assertRaises(RecoveryError):
            RecoveryStore(self.path, changed)
        journal.unlink()
        with self.assertRaises(FileNotFoundError):
            RecoveryStore(self.path, CONFIG)
        with self.assertRaises(FileExistsError):
            RecoveryStore.initialize(self.path, CONFIG)

    def test_recovery_state_symlink_and_second_owner_refused(self):
        journal = self.path / "recovery.json"
        with RecoveryStore(self.path, CONFIG):
            with self.assertRaises(OSError):
                RecoveryStore(self.path, CONFIG)
        copy = self.path / "other"
        journal.rename(copy)
        journal.symlink_to(copy)
        with self.assertRaises(OSError):
            RecoveryStore(self.path, CONFIG)

    def test_production_serve_requires_durable_state(self):
        broker = h.Broker(CONFIG)
        with self.assertRaisesRegex(RecoveryError, "durable_recovery_required"):
            h.serve(broker, Path(self.temp.name) / "never-created.sock")

    def test_epoch_exhaustion_blocks_reservation_without_reset(self):
        from recovery_state import MAX_EPOCH
        with RecoveryStore(self.path, CONFIG) as store:
            state = deepcopy(store.state)
            state["epoch"] = MAX_EPOCH
            store._commit(state)
            with self.assertRaisesRegex(RecoveryError, "epoch_exhausted"):
                store.allocate_epoch()


if __name__ == "__main__":
    unittest.main()
