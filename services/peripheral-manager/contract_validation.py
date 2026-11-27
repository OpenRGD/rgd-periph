"""Local validation of the seven immutable Draft 2020-12 candidate schemas.

Fail closed if dependencies, schema files or required date-time format checking
are unavailable. No retrieval of remote schemas or instance data.
"""
from functools import lru_cache
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "contracts" / "console" / "v1"
NAMES = frozenset(("descriptor", "status", "action", "action-result",
                   "host-request", "host-response", "host-allowlist"))
FORMATS = FormatChecker()
if "date-time" not in FORMATS.checkers:
    raise RuntimeError("required_rfc3339_format_checker_unavailable")


@lru_cache(maxsize=len(NAMES))
def validator(name: str):
    if name not in NAMES:
        raise ValueError("unknown_candidate_contract")
    path = SCHEMA_ROOT / f"peripheral-{name}.v0.1-candidate.schema.json"
    schema = json.loads(path.read_text(encoding="utf-8"))
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("unexpected_schema_dialect")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FORMATS)


def validate_instance(name: str, value) -> None:
    # Never log a validation exception's raw payload or private config.
    if not validator(name).is_valid(value):
        raise ValueError(f"invalid_{name.replace('-', '_')}_schema")
