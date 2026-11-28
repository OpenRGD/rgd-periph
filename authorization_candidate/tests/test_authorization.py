"""Offline candidate only: independent expected decisions, no host/IPC calls."""
from copy import deepcopy
from dataclasses import replace
import unittest

from authorization_candidate.evaluate import (
    Principal, Scope, TrustedContext, VerifiedReceipt, evaluate, valid, validator,
)


def fixture(cap="acquisition.start", privacy="sensor_observation", mode="direct"):
    p = Principal("fixture-issuer", "fixture-operator", "fixture-session", "fixture-client")
    scope = Scope("fixture-host", "fixture-robot", "fixture-periph")
    intent = {
        "schema_version": "openrgd.periph.authz-intent.v0.1-candidate",
        "request_id": "d64fda80-3941-40f1-9c31-2f100a5578bc",
        "grant_id": "fixture-grant", "audience": "fixture-broker", "scope": scope.record(),
        "capability": cap, "policy_revision": 7,
        "expected_status_revision": None if cap == "peripheral.status.read" else 42,
        "clock_domain": "fixture-boot", "issued_ms": 990, "expires_ms": 1500,
        "grants_authority": False, "actuation_enabled": False,
    }
    grant = {"grant_id": "fixture-grant", "principal": p.record(), "mode": mode,
             "scope": scope.record(), "capability": cap, "audience": "fixture-broker",
             "valid_from_ms": 0, "expires_ms": 2000}
    policy = {"schema_version": "openrgd.periph.authz-policy.v0.1-candidate",
              "authority_id": "fixture-policy", "consent_authority_id": "fixture-consent",
              "revision": 7, "audience": "fixture-broker", "clock_domain": "fixture-boot",
              "issued_ms": 900, "expires_ms": 1700, "grants": [grant],
              "revoked_grant_ids": [], "grants_authority": False, "actuation_enabled": False}
    confirmation = VerifiedReceipt("fixture-issuer", p, deepcopy(intent), 995, 1400)
    context = TrustedContext(p, mode, "fixture-broker", "fixture-boot", 1000, 1600, True,
                             "fixture-policy", 7, scope, True,
                             frozenset(("peripheral.status.read", "acquisition.start",
                                        "acquisition.stop")), privacy, 42, True, True,
                             confirmation, None)
    return intent, policy, context


class AuthorizationCandidate(unittest.TestCase):
    def check(self, intent, policy, context, reason):
        # In these fixtures ONLY, independently supplied policy is the verified snapshot.
        if type(context) is TrustedContext:
            context = replace(context, verified_policy=deepcopy(policy))
        result = evaluate(intent, policy, context)
        self.assertTrue(valid("decision", result), result)
        self.assertEqual(result["reason"], reason)
        self.assertEqual(result["decision"], "eligible_for_host_review" if reason == "eligible" else "deny")
        self.assertIs(result["grants_authority"], False)
        self.assertIs(result["actuation_enabled"], False)

    def test_schema_meta_and_happy_paths(self):
        for name in ("intent", "policy", "decision"):
            validator(name)
        for cap in ("peripheral.status.read", "acquisition.start", "acquisition.stop"):
            for mode in ("direct", "delegated"):
                with self.subTest(cap=cap, mode=mode):
                    self.check(*fixture(cap=cap, mode=mode), "eligible")

    def test_untrusted_json_cannot_supply_context(self):
        i, p, c = fixture()
        self.check(i, p, {"session_active": True, "role": "admin"}, "unverified_context")
        self.check(i, p, None, "unverified_context")

    def test_closed_intent_and_unknown_capabilities(self):
        for change in (
            {"role": "admin"}, {"verified": True}, {"grants_authority": True},
            {"actuation_enabled": True}, {"capability": "motor.enable"},
            {"capability": "observation.read"}, {"capability": "*"},
            {"capability": "publisher.exec"}, {"scope": {"host_id": "*"}},
            {"expected_status_revision": True}, {"expected_status_revision": 42.0},
            {"expected_status_revision": 1 << 53}, {"request_id": "not-uuid"},
            {"request_id": "d64fda80-3941-40f1-9c31-2f100a5578bc\n"},
            {"expected_status_revision": None}, {"schema_version": "future"},
        ):
            with self.subTest(change=change):
                i, p, c = fixture()
                self.check(dict(i, **change), p, c, "invalid_intent")
        i, p, c = fixture(cap="peripheral.status.read")
        self.check(dict(i, expected_status_revision=42), p, c, "invalid_intent")

    def test_policy_shape_and_duplicate_grants(self):
        for change in ({"grants_authority": True}, {"wildcard": True},
                       {"revision": True}, {"revision": 7.0},
                       {"revoked_grant_ids": ["fixture-grant", "fixture-grant"]}):
            with self.subTest(change=change):
                i, p, c = fixture()
                self.check(i, dict(p, **change), c, "invalid_policy")
        i, p, c = fixture()
        p["grants"].append(deepcopy(p["grants"][0]))
        self.check(i, p, c, "invalid_policy")

    def test_clock_audience_revision_and_lifetime(self):
        for field, value, reason in (
            ("clock_domain", "other-boot", "clock_unavailable"),
            ("audience", "other-broker", "wrong_audience"),
            ("policy_revision", 6, "stale_policy"),
            ("expires_ms", 1000, "expired_intent"),
            ("issued_ms", 1001, "expired_intent"),
            ("expires_ms", 31000, "expired_intent"),
        ):
            with self.subTest(field=field, value=value):
                i, p, c = fixture()
                self.check(dict(i, **{field: value}), p, c, reason)
        for field, value, reason in (
            ("clock_domain", "other-boot", "clock_unavailable"),
            ("audience", "other-broker", "wrong_audience"),
            ("authority_id", "other-authority", "unverified_context"),
            ("revision", 6, "stale_policy"),
            ("expires_ms", 1000, "stale_policy"),
            ("issued_ms", 1001, "stale_policy"),
            ("expires_ms", 62000, "stale_policy"),
        ):
            with self.subTest(field=field, value=value):
                i, p, c = fixture()
                self.check(i, dict(p, **{field: value}), c, reason)

    def test_current_context_and_revocation(self):
        for change, reason in (
            ({"current_policy_revision": 8}, "stale_policy"),
            ({"session_active": False}, "revoked_session"),
            ({"session_expires_ms": 1000}, "revoked_session"),
            ({"now_ms": True}, "unverified_context"),
            ({"session_active": 1}, "unverified_context"),
            ({"mode": "transitive"}, "unverified_context"),
        ):
            with self.subTest(change=change):
                i, p, c = fixture()
                self.check(i, p, replace(c, **change), reason)
        i, p, c = fixture()
        self.check(i, dict(p, revoked_grant_ids=["fixture-grant"]), c, "revoked_grant")
        self.check(dict(i, grant_id="missing-grant"), p, c, "unknown_grant")
        self.check(i, dict(p, grants=[]), c, "unknown_grant")

    def test_scope_and_principal_are_not_interchangeable(self):
        for field in ("issuer_id", "subject_id", "session_id", "client_id"):
            with self.subTest(field=field):
                i, p, c = fixture(mode="delegated")
                self.check(i, p, replace(c, principal=replace(c.principal, **{field: "other"})),
                           "grant_mismatch")
        for field in ("host_id", "robot_id", "peripheral_id"):
            with self.subTest(field=field):
                i, p, c = fixture()
                scope = dict(i["scope"], **{field: "other"})
                self.check(dict(i, scope=scope), p, c, "grant_mismatch")
                self.check(i, p, replace(c, scope=replace(c.scope, **{field: "other"})),
                           "resource_unavailable")
        i, p, c = fixture()
        self.check(i, p, replace(c, mode="delegated"), "grant_mismatch")
        self.check(dict(i, capability="acquisition.stop"), p, c, "grant_mismatch")
        p["grants"][0]["expires_ms"] = 1000
        self.check(i, p, c, "grant_expired")

    def test_status_does_not_require_lifecycle_grant_or_unlock_quarantine(self):
        i, p, c = fixture(cap="peripheral.status.read", privacy="unknown")
        self.check(i, p, replace(c, recovery_ready=False, confirmation=None), "eligible")
        self.check(i, p, replace(c, audit_ready=False), "audit_unavailable")
        self.check(i, p, replace(c, resource_capabilities=frozenset(("acquisition.start",))),
                   "capability_unavailable")

    def test_lifecycle_host_conditions_and_confirmation(self):
        for change, reason in (
            ({"resource_available": False}, "resource_unavailable"),
            ({"resource_capabilities": frozenset(("peripheral.status.read",))}, "capability_unavailable"),
            ({"audit_ready": False}, "audit_unavailable"),
            ({"recovery_ready": False}, "recovery_required"),
            ({"status_revision": 43}, "stale_status_revision"),
            ({"confirmation": None}, "confirmation_required"),
        ):
            with self.subTest(change=change):
                i, p, c = fixture()
                self.check(i, p, replace(c, **change), reason)
        i, p, c = fixture()
        for receipt in (
            replace(c.confirmation, expires_ms=1000),
            replace(c.confirmation, issued_ms=1001),
            replace(c.confirmation, expires_ms=1501),
            replace(c.confirmation, authority_id="other-issuer"),
            replace(c.confirmation, principal=replace(c.principal, session_id="other-session")),
            replace(c.confirmation, intent=dict(i, request_id="572ce555-19b5-45af-8845-3562072632f2")),
            replace(c.confirmation, intent=dict(i, expires_ms=1499)),
        ):
            with self.subTest(receipt=receipt):
                self.check(i, p, replace(c, confirmation=receipt), "confirmation_required")

    def test_privacy_consent_is_separate_from_confirmation(self):
        i, p, c = fixture(privacy="privacy_sensitive_media")
        self.check(i, p, c, "consent_required")
        self.check(i, p, replace(c, consent=c.confirmation), "consent_required")
        consent = VerifiedReceipt("fixture-consent", c.principal, deepcopy(i), 995, 1400)
        self.check(i, p, replace(c, consent=consent), "eligible")
        self.check(i, p, replace(c, consent=replace(consent, expires_ms=1000)), "consent_required")
        self.check(i, p, replace(c, consent=replace(consent, intent=dict(i, audience="other"))),
                   "consent_required")
        self.check(i, p, replace(c, privacy_class="unknown", consent=consent), "consent_required")
        self.check(*fixture(cap="acquisition.stop", privacy="privacy_sensitive_media"), "eligible")
        self.check(*fixture(cap="acquisition.stop", privacy="unknown"), "eligible")

    def test_exact_policy_snapshot_not_revision_alone(self):
        i, p, c = fixture()
        c = replace(c, verified_policy=deepcopy(p))
        self.assertEqual(evaluate(i, p, c)["reason"], "eligible")
        altered = deepcopy(p)
        altered["grants"][0]["expires_ms"] += 1
        self.assertEqual(evaluate(i, altered, c)["reason"], "unverified_context")
        self.assertEqual(evaluate(i, p, replace(c, verified_policy=None))["reason"], "unverified_context")

    def test_no_positive_decision_without_independent_context(self):
        i, p, c = fixture()
        report = evaluate(i, p, replace(c, verified_policy=deepcopy(p)))
        self.assertFalse(valid("decision", dict(report, grants_authority=True)))
        self.assertFalse(valid("decision", dict(report, actuation_enabled=True)))
        self.assertFalse(valid("decision", dict(report, decision="allow")))
        self.assertFalse(valid("decision", dict(report, reason="revoked_grant")))
        self.assertFalse(valid("decision", dict(report, decision="deny")))


if __name__ == "__main__":
    unittest.main()
