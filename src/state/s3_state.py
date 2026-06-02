"""Single-object S3 state store (spec §5).

One GET + one PUT per invocation. Holds the de-dup / execute-once message ids plus a
bounded audit trail. For local tests, pass an explicit `client` (e.g. a fake) or use
`InMemoryStateStore`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from config import AUDIT_MAX_ENTRIES


@dataclass
class State:
    last_alerted_message_id: str | None = None
    last_executed_message_id: str | None = None
    last_skipped_message_id: str | None = None
    last_rejected_message_id: str | None = None
    last_signal: str | None = None
    last_action_at: str | None = None
    audit: list[dict] = field(default_factory=list)

    @property
    def is_first_run(self) -> bool:
        """No baseline yet ⇒ first run (record signal, trade nothing) per §3/§5."""
        return self.last_executed_message_id is None

    def is_handled(self, message_id: str) -> bool:
        """True if this signal was already executed OR vetoed (skip)."""
        return message_id in (self.last_executed_message_id, self.last_skipped_message_id)

    def add_audit(self, entry: dict) -> None:
        self.audit.append(entry)
        if len(self.audit) > AUDIT_MAX_ENTRIES:
            self.audit = self.audit[-AUDIT_MAX_ENTRIES:]

    def touch(self, signal: str | None = None) -> None:
        self.last_action_at = datetime.now(timezone.utc).isoformat()
        if signal is not None:
            self.last_signal = signal

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "State":
        data = json.loads(raw) if raw else {}
        return cls(
            last_alerted_message_id=data.get("last_alerted_message_id"),
            last_executed_message_id=data.get("last_executed_message_id"),
            last_skipped_message_id=data.get("last_skipped_message_id"),
            last_rejected_message_id=data.get("last_rejected_message_id"),
            last_signal=data.get("last_signal"),
            last_action_at=data.get("last_action_at"),
            audit=data.get("audit", []),
        )


class S3StateStore:
    def __init__(self, bucket: str, key: str, region: str, client=None):
        self._bucket = bucket
        self._key = key
        self._region = region
        self._client = client

    def _s3(self):
        if self._client is None:
            import boto3  # lazy

            self._client = boto3.client("s3", region_name=self._region)
        return self._client

    def load(self) -> State:
        try:
            obj = self._s3().get_object(Bucket=self._bucket, Key=self._key)
            return State.from_json(obj["Body"].read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 — NoSuchKey or first run ⇒ empty state
            if "NoSuchKey" in type(e).__name__ or "NoSuchKey" in str(e):
                return State()
            # ClientError 404 path
            code = getattr(getattr(e, "response", {}), "get", lambda *_: None)("Error")
            if isinstance(code, dict) and code.get("Code") in ("NoSuchKey", "404"):
                return State()
            raise

    def save(self, state: State) -> None:
        self._s3().put_object(
            Bucket=self._bucket,
            Key=self._key,
            Body=state.to_json().encode("utf-8"),
            ContentType="application/json",
        )


class InMemoryStateStore:
    """Test double — same interface as S3StateStore."""

    def __init__(self, state: State | None = None):
        self.state = state or State()

    def load(self) -> State:
        return self.state

    def save(self, state: State) -> None:
        self.state = state
