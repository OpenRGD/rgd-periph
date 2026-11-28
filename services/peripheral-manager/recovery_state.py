"""Private durable recovery journal for the local PERIPH broker.

No device or systemd calls. One owner per state directory. State is not a
tamper-proof audit log, nor a replacement for independent host reconciliation.
"""
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
from datetime import datetime, timezone

if __package__:
    from .contract_validation import validate_instance
else:
    from contract_validation import validate_instance

VERSION = "periph.recovery.v0.1-candidate"
EPOCH_STRIDE = 1 << 32
MAX_EPOCH = (1 << 21) - 1
MAX_BYTES = 128 * 1024
STATE = "recovery.json"
LOCK = "owner.lock"


class RecoveryError(ValueError):
    """Sanitized local recovery failure; never expose raw journal content."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def scope(key):
    return "/".join(key)


def _strict_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RecoveryError("duplicate_recovery_key")
        result[key] = value
    return result


def _reject_constant(_):
    raise RecoveryError("invalid_recovery_number")


def _private_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
        raise RecoveryError("unsafe_recovery_file")


def _directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise RecoveryError("unsafe_recovery_directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


class RecoveryStore:
    def __init__(self, path: Path, config: dict):
        self.path = Path(path)
        self.config_digest = digest(config)
        self.scopes = {scope((r["robot_id"], r["peripheral_id"]))
                       for r in config["services"]}
        self.dirfd = _directory(self.path)
        self.lockfd = None
        self.healthy = False
        try:
            self.lockfd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                  dir_fd=self.dirfd)
            _private_file(self.lockfd)
            fcntl.flock(self.lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.state = self._read()
            self.healthy = True
        except BaseException:
            self.close()
            raise

    @classmethod
    def initialize(cls, path: Path, config: dict):
        """Creation-only: no reset, overwrite or implicit recovery."""
        path = Path(path)
        # Validate before allocating anything; caller validates host ownership too.
        validate_instance("host-allowlist", config)
        path.mkdir(mode=0o700)
        dirfd = _directory(path)
        try:
            fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=dirfd)
            try:
                os.fchmod(fd, 0o600)
                os.fsync(fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                payload = {"version": VERSION, "config_sha256": digest(config),
                           "epoch": 0, "pending": {}, "last_ack": {}}
                cls._atomic(dirfd, payload)
                # Persist the newly created directory entry as well.
                parentfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(parentfd)
                finally:
                    os.close(parentfd)
            finally:
                os.close(fd)
        finally:
            os.close(dirfd)

    @staticmethod
    def _atomic(dirfd, payload):
        data = canonical({"payload": payload, "sha256": digest(payload)}) + b"\n"
        if len(data) > MAX_BYTES:
            raise RecoveryError("recovery_size_limit")
        name = ".pending-" + secrets.token_hex(16)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dirfd)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, STATE, src_dir_fd=dirfd, dst_dir_fd=dirfd)
            os.fsync(dirfd)
        finally:
            try:
                os.unlink(name, dir_fd=dirfd)
            except FileNotFoundError:
                pass

    def _read(self):
        fd = os.open(STATE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=self.dirfd)
        with os.fdopen(fd, "rb") as stream:
            _private_file(stream.fileno())
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise RecoveryError("recovery_size_limit")
        try:
            wrapper = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_pairs,
                                 parse_constant=_reject_constant)
            if not isinstance(wrapper, dict) or set(wrapper) != {"payload", "sha256"}:
                raise RecoveryError("invalid_recovery_envelope")
            data = wrapper["payload"]
            if digest(data) != wrapper["sha256"]:
                raise RecoveryError("recovery_digest_mismatch")
            self._validate(data)
            return data
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise RecoveryError("invalid_or_incompatible_recovery_state") from error

    def _validate(self, data):
        if (not isinstance(data, dict)
                or set(data) != {"version", "config_sha256", "epoch", "pending", "last_ack"}
                or data["version"] != VERSION or data["config_sha256"] != self.config_digest
                or type(data["epoch"]) is not int or not 0 <= data["epoch"] <= MAX_EPOCH):
            raise RecoveryError("invalid_recovery_header")
        for field in ("pending", "last_ack"):
            if not isinstance(data[field], dict) or not set(data[field]) <= self.scopes:
                raise RecoveryError("invalid_recovery_scope")
        for key, request in data["pending"].items():
            validate_instance("host-request", request)
            revision = request.get("expected_status_revision")
            if (scope((request["robot_id"], request["peripheral_id"])) != key
                    or request["action"] not in ("acquisition.start", "acquisition.stop")
                    or type(revision) is not int
                    or not EPOCH_STRIDE <= revision < (data["epoch"] + 1) * EPOCH_STRIDE):
                raise RecoveryError("invalid_pending_intent")
        for ack in data["last_ack"].values():
            if (not isinstance(ack, dict)
                    or set(ack) != {"request_id", "review_ref", "recorded_at", "kind"}
                    or ack["kind"] != "external_review_asserted"
                    or not isinstance(ack["request_id"], str)
                    or not re.fullmatch(r"[0-9a-fA-F-]{36}", ack["request_id"])
                    or not isinstance(ack["review_ref"], str)
                    or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", ack["review_ref"])
                    or not isinstance(ack["recorded_at"], str)):
                raise RecoveryError("invalid_recovery_ack")

    def _commit(self, next_state):
        if not self.healthy:
            raise RecoveryError("recovery_unavailable")
        try:
            self._validate(next_state)
            self._atomic(self.dirfd, next_state)
        except BaseException:
            # A failed write/fsync may already have changed the on-disk state.
            # Do not continue admitting operations from uncertain memory.
            self.healthy = False
            raise
        self.state = next_state

    def allocate_epoch(self):
        if self.state["epoch"] >= MAX_EPOCH:
            raise RecoveryError("epoch_exhausted")
        next_state = deepcopy(self.state)
        next_state["epoch"] += 1
        self._commit(next_state)
        low = self.state["epoch"] * EPOCH_STRIDE
        return low, low + EPOCH_STRIDE - 1

    def pending_keys(self):
        return {tuple(key.split("/")) for key in self.state["pending"]}

    def begin(self, key, request):
        name = scope(key)
        if name in self.state["pending"]:
            raise RecoveryError("reconciliation_required")
        next_state = deepcopy(self.state)
        next_state["pending"][name] = deepcopy(request)
        self._commit(next_state)

    def complete(self, key, request_id):
        name = scope(key)
        next_state = deepcopy(self.state)
        pending = next_state["pending"].get(name)
        if pending is None or pending["request_id"] != request_id:
            raise RecoveryError("pending_intent_mismatch")
        del next_state["pending"][name]
        self._commit(next_state)

    def acknowledge(self, key, request_id, review_ref, confirmation):
        """Offline manual recovery acknowledgement, not verified job evidence."""
        name = scope(key)
        expected = f"ACK RECONCILED {name} {request_id}"
        if confirmation != expected or not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", review_ref):
            raise RecoveryError("explicit_reconciliation_required")
        next_state = deepcopy(self.state)
        pending = next_state["pending"].get(name)
        if pending is None or pending["request_id"] != request_id:
            raise RecoveryError("pending_intent_mismatch")
        del next_state["pending"][name]
        next_state["last_ack"][name] = {
            "request_id": request_id, "review_ref": review_ref,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "kind": "external_review_asserted",
        }
        self._commit(next_state)

    def inspect(self):
        return deepcopy({"epoch": self.state["epoch"],
                         "pending": self.state["pending"],
                         "last_ack": self.state["last_ack"],
                         "actuation_enabled": False})

    def close(self):
        self.healthy = False
        if self.lockfd is not None:
            os.close(self.lockfd)
            self.lockfd = None
        if self.dirfd is not None:
            os.close(self.dirfd)
            self.dirfd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
