"""PERIPH-0.3A offline reference evaluator. NOT an authentication or runtime API.

Only a future independently reviewed adapter may establish trusted context.
No broker imports, no filesystem writes, no network, no dispatch, no credentials.
"""
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contracts" / "authorization" / "v0.1-candidate"
MAX_SAFE = (1 << 53) - 1
INTENT_TTL = 30_000
POLICY_TTL = 60_000
TOKEN = re.compile(r"[a-z][a-z0-9._:-]{0,99}")
SCOPE_ID = re.compile(r"[a-z][a-z0-9-]{0,79}")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
STATUS = "peripheral.status.read"
CAPS = frozenset((STATUS, "acquisition.start", "acquisition.stop"))


@dataclass(frozen=True)
class Principal:
    issuer_id: str
    subject_id: str
    session_id: str
    client_id: str

    def record(self):
        return dict(issuer_id=self.issuer_id, subject_id=self.subject_id,
                    session_id=self.session_id, client_id=self.client_id)


@dataclass(frozen=True)
class Scope:
    host_id: str
    robot_id: str
    peripheral_id: str

    def record(self):
        return dict(host_id=self.host_id, robot_id=self.robot_id,
                    peripheral_id=self.peripheral_id)


@dataclass(frozen=True)
class VerifiedReceipt:
    """Test-only projection of an externally verified receipt, not a proof format."""
    authority_id: str
    principal: Principal
    intent: dict
    issued_ms: int
    expires_ms: int


@dataclass(frozen=True)
class TrustedContext:
    """Adapter-owned inputs; never deserialize caller-supplied JSON as this type."""
    principal: Principal
    mode: str
    audience: str
    clock_domain: str
    now_ms: int
    session_expires_ms: int
    session_active: bool
    policy_authority_id: str
    current_policy_revision: int
    scope: Scope
    resource_available: bool
    resource_capabilities: frozenset
    privacy_class: str
    status_revision: int
    audit_ready: bool
    recovery_ready: bool
    confirmation: VerifiedReceipt | None = None
    consent: VerifiedReceipt | None = None
    verified_policy: dict | None = None


@lru_cache(maxsize=3)
def validator(kind):
    if kind not in ("intent", "policy", "decision"):
        raise ValueError("unsupported_candidate")
    schema = json.loads((SCHEMAS / (kind + ".schema.json")).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def valid_token(value, pattern=TOKEN):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def exact_int(value):
    return type(value) is int and 0 <= value <= MAX_SAFE


def exact_tree(value):
    """Reject mathematical integer floats, non-JSON types and unbounded nesting."""
    def check(v, depth):
        if depth > 12:
            return False
        if type(v) is dict:
            return len(v) <= 128 and all(type(k) is str and len(k) <= 128 and
                                         check(item, depth + 1) for k, item in v.items())
        if type(v) is list:
            return len(v) <= 128 and all(check(item, depth + 1) for item in v)
        if type(v) is str:
            return len(v) <= 128 and v.isascii() and all(32 <= ord(c) <= 126 for c in v)
        return v is None or type(v) is bool or exact_int(v)
    return check(value, 0)


def valid(kind, value):
    return exact_tree(value) and validator(kind).is_valid(value)


def principal_ok(value):
    return type(value) is Principal and all(valid_token(v) for v in value.record().values())


def scope_ok(value):
    return type(value) is Scope and all(valid_token(v, SCOPE_ID) for v in value.record().values())


def context_ok(c):
    return (type(c) is TrustedContext and principal_ok(c.principal) and scope_ok(c.scope)
            and c.mode in ("direct", "delegated")
            and all(valid_token(v) for v in (c.audience, c.clock_domain, c.policy_authority_id))
            and all(exact_int(v) for v in (c.now_ms, c.session_expires_ms,
                                           c.current_policy_revision, c.status_revision))
            and all(type(v) is bool for v in (c.session_active, c.resource_available,
                                             c.audit_ready, c.recovery_ready))
            and type(c.resource_capabilities) is frozenset
            and c.resource_capabilities <= CAPS
            and c.privacy_class in ("sensor_observation", "privacy_sensitive_media", "unknown"))


def receipt_matches(receipt, authority, c, intent):
    return (type(receipt) is VerifiedReceipt and receipt.authority_id == authority
            and receipt.principal == c.principal
            and valid("intent", receipt.intent) and receipt.intent == intent
            and exact_int(receipt.issued_ms) and exact_int(receipt.expires_ms)
            and intent["issued_ms"] <= receipt.issued_ms <= c.now_ms < receipt.expires_ms
            and receipt.expires_ms <= intent["expires_ms"]
            and receipt.expires_ms <= c.session_expires_ms)


def evaluate(intent, policy, context):
    """Return a non-authoritative eligibility report, never an execution permit."""
    req_id = intent.get("request_id") if type(intent) is dict else None
    req_id = req_id if valid_token(req_id, UUID) else None
    revision = None

    def report(reason):
        return dict(schema_version="openrgd.periph.authz-decision.v0.1-candidate",
                    request_id=req_id, policy_revision=revision,
                    decision="eligible_for_host_review" if reason == "eligible" else "deny",
                    reason=reason, grants_authority=False, actuation_enabled=False)

    if not valid("intent", intent):
        return report("invalid_intent")
    if not valid("policy", policy):
        return report("invalid_policy")
    if not context_ok(context):
        return report("unverified_context")
    c = context
    revision = c.current_policy_revision
    now = c.now_ms
    if (policy["authority_id"] != c.policy_authority_id
            or not valid("policy", c.verified_policy) or policy != c.verified_policy):
        return report("unverified_context")
    if intent["clock_domain"] != c.clock_domain or policy["clock_domain"] != c.clock_domain:
        return report("clock_unavailable")
    if intent["audience"] != c.audience or policy["audience"] != c.audience:
        return report("wrong_audience")
    if (intent["policy_revision"] != revision or policy["revision"] != revision
            or not policy["issued_ms"] <= now < policy["expires_ms"]
            or not 0 < policy["expires_ms"] - policy["issued_ms"] <= POLICY_TTL):
        return report("stale_policy")
    if (not intent["issued_ms"] <= now < intent["expires_ms"]
            or not 0 < intent["expires_ms"] - intent["issued_ms"] <= INTENT_TTL):
        return report("expired_intent")
    if not c.session_active or now >= c.session_expires_ms:
        return report("revoked_session")
    grants = {g["grant_id"]: g for g in policy["grants"]}
    if len(grants) != len(policy["grants"]):
        return report("invalid_policy")
    if intent["grant_id"] in policy["revoked_grant_ids"]:
        return report("revoked_grant")
    g = grants.get(intent["grant_id"])
    if g is None:
        return report("unknown_grant")
    if (g["principal"] != c.principal.record() or g["mode"] != c.mode
            or g["audience"] != c.audience or g["scope"] != intent["scope"]
            or g["capability"] != intent["capability"]):
        return report("grant_mismatch")
    if not g["valid_from_ms"] <= now < g["expires_ms"]:
        return report("grant_expired")
    if not c.resource_available or c.scope.record() != intent["scope"]:
        return report("resource_unavailable")
    if intent["capability"] not in c.resource_capabilities:
        return report("capability_unavailable")
    if not c.audit_ready:
        return report("audit_unavailable")
    if intent["capability"] == STATUS:
        return report("eligible")
    if not c.recovery_ready:
        return report("recovery_required")
    if intent["expected_status_revision"] != c.status_revision:
        return report("stale_status_revision")
    if not receipt_matches(c.confirmation, c.principal.issuer_id, c, intent):
        return report("confirmation_required")
    if c.privacy_class == "unknown" and intent["capability"] == "acquisition.start":
        return report("consent_required")
    if c.privacy_class == "privacy_sensitive_media" and intent["capability"] == "acquisition.start":
        if not receipt_matches(c.consent, policy["consent_authority_id"], c, intent):
            return report("consent_required")
    return report("eligible")
