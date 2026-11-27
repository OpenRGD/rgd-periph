"""Standalone schema and managed-host policy conformance; no physical host calls."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "peripheral-manager"))
from contract_validation import NAMES, validate_instance, validator
import control_cli as cli
import host_broker as broker

WIRE = json.loads((ROOT / "conformance" / "wire-cases.json").read_text(encoding="utf-8"))
POLICY = json.loads((ROOT / "conformance" / "policy-cases.json").read_text(encoding="utf-8"))


def document(name):
    return deepcopy(WIRE["documents"][name])


def materialize(case):
    data = document(case["contract"])
    for change in case["changes"]:
        parts = change["path"].split("/")
        parent = data
        for part in parts[:-1]:
            parent = parent[int(part) if isinstance(parent, list) else part]
        key = int(parts[-1]) if isinstance(parent, list) else parts[-1]
        if change["op"] == "remove":
            del parent[key]
        elif change["op"] == "set":
            parent[key] = deepcopy(change["value"])
        else:
            raise AssertionError("unknown fixture operation")
    return data


class SchemaConformance(unittest.TestCase):
    def test_all_seven_schemas_and_positive_negative_wire_vectors(self):
        self.assertEqual(set(WIRE["documents"]), NAMES)
        cases = WIRE["cases"]
        self.assertEqual(len(cases), 60)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        for name in sorted(NAMES):
            validator(name)
            self.assertEqual({c["valid"] for c in cases if c["contract"] == name},
                             {True, False})
        for case in cases:
            with self.subTest(id=case["id"]):
                value = materialize(case)
                self.assertEqual(validator(case["contract"]).is_valid(value),
                                 case["valid"])
                if case["valid"]:
                    validate_instance(case["contract"], value)
                else:
                    with self.assertRaises(ValueError):
                        validate_instance(case["contract"], value)

    def test_unchanged_historical_fixtures_use_full_schema_validator(self):
        root = ROOT / "examples" / "peripherals"
        # Resolve exact archived filenames by schema_version, rather than assume
        # that historical example filenames equal their schema names.
        files = sorted(root.glob("*.json"))
        self.assertEqual(len(files), 5)
        expected = {"descriptor": 2, "status": 1, "action": 1, "action-result": 1}
        counts = {}
        for file in files:
            data = json.loads(file.read_text(encoding="utf-8"))
            name = data["schema_version"].removeprefix("console.peripheral-").removesuffix(".v0.1-candidate")
            validate_instance(name, data)
            counts[name] = counts.get(name, 0) + 1
        self.assertEqual(counts, expected)

    def test_json_schema_integer_vs_stricter_runtime_revision_profile(self):
        request = document("host-request")
        request["expected_status_revision"] = 42.0
        validate_instance("host-request", request)
        manager = broker.Broker(document("host-allowlist"),
                                runner=lambda *a, **k: self.fail("unexpected subprocess"))
        self.assertEqual(manager.process(request)["error_code"], "expected_revision_required")
        request["expected_status_revision"] = 1 << 53
        validate_instance("host-request", request)
        self.assertEqual(manager.process(request)["error_code"], "expected_revision_required")

    def test_schema_valid_allowlist_still_requires_exclusive_ownership(self):
        config = document("host-allowlist")
        config["services"].append(deepcopy(config["services"][0]))
        validate_instance("host-allowlist", config)
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "allowlist.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "duplicate_exclusive_owner"):
                broker.read_allowlist(path)
            config["services"].pop()
            path.write_text(json.dumps(config), encoding="utf-8")
            self.assertEqual(broker.read_allowlist(path), config)

    def test_response_schema_is_not_authority_or_request_correlation(self):
        response = document("host-response")
        request = document("host-request")
        self.assertEqual(cli.validate_reply(response, request), response)
        for field, value in (
            ("robot_id", "other-robot"),
            ("peripheral_id", "other-imu"),
            ("request_id", "572ce555-19b5-45af-8845-3562072632f2"),
            ("status_revision", 42),
            ("status_revision", 1 << 53),
        ):
            changed = dict(response, **{field: value})
            validate_instance("host-response", changed)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                cli.validate_reply(changed, request)
        changed = deepcopy(response)
        changed["publisher"]["lifecycle"] = "stopped"
        validate_instance("host-response", changed)
        with self.assertRaises(ValueError):
            cli.validate_reply(changed, request)


class ManagedHostConformance(unittest.TestCase):
    def test_shared_policy_vectors_through_actual_broker(self):
        cases = POLICY["cases"]
        self.assertEqual(len(cases), 32)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        reasons = {
            "stale_status_revision": "stale_revision",
            "manager_unavailable": "manager_unavailable",
            "service_unavailable": "manager_unavailable",
            "transition_in_progress": "transition",
            "device_missing_or_unverified": "device_unavailable",
            "unregistered_peripheral": "scope",
            "invalid_request_id": "request_id",
            "expected_revision_required": "invalid_revision",
            "revision_exhausted": "revision_exhausted",
        }
        for case in cases:
            with self.subTest(id=case["id"]):
                i = case["input"]
                state = {"lifecycle": i["lifecycle"]}
                dispatches = []
                config = document("host-allowlist")
                service = config["services"][0]
                key = (service["robot_id"], service["peripheral_id"])
                manager = broker.Broker(config, runner=lambda *a, **k: self.fail("unexpected subprocess"))
                manager.revisions[key] = i["observed_revision"]

                def probe(_service, _runner):
                    return state["lifecycle"], False, i["manager_available"]

                def run_unit(action, unit, _runner):
                    self.assertEqual(unit, service["service_ref"])
                    dispatches.append(action)
                    state["lifecycle"] = "running" if action == "start" else "stopped"
                    return True

                request = document("host-request")
                request.update(action=i["action"], robot_id=i["robot_id"],
                               peripheral_id=i["peripheral_id"], request_id=i["request_id"],
                               expected_status_revision=i["expected_revision"])
                with patch.object(broker, "systemd_show", side_effect=probe), \
                     patch.object(broker, "device_identity",
                                  return_value=(i["presence"], i["binding_verified"])), \
                     patch.object(broker, "run_unit", side_effect=run_unit):
                    result = manager.process(request, may_dispatch=lambda: True)

                validate_instance("host-response", result)
                disposition = result["disposition"]
                if disposition == "accepted":
                    actual = {"decision": "dispatch", "reason": None}
                elif disposition == "no_op":
                    actual = {"decision": "no_op", "reason": None}
                elif disposition == "rejected":
                    actual = {"decision": "reject", "reason": reasons[result["error_code"]]}
                else:
                    self.fail(f"unexpected disposition {disposition}")
                self.assertEqual(actual, case["expected"])
                if actual["decision"] == "dispatch":
                    self.assertEqual(dispatches, [i["action"].split(".")[1]])
                else:
                    self.assertEqual(dispatches, [])
                self.assertFalse(result["actuation_enabled"])


if __name__ == "__main__":
    unittest.main()
