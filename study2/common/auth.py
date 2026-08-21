"""Contextual HMAC and replay state; not a proof of computation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Envelope:
    protocol_version: str
    owner_id: str
    executor_id: str
    task_id: str
    round_id: int
    batch_id: str
    cut_id: str
    model_state_hash: str
    sample_count: int
    sequence_number: int
    nonce: str
    payload_hash: str

    @classmethod
    def make(
        cls,
        *,
        owner_id,
        executor_id,
        task_id,
        round_id,
        batch_id,
        cut_id,
        model_state_hash,
        sample_count,
        sequence_number,
        payload,
        nonce=None,
    ):
        nonce = os.urandom(16) if nonce is None else nonce

        return cls(
            "dfl-exp/1",
            owner_id,
            executor_id,
            task_id,
            int(round_id),
            str(batch_id),
            cut_id,
            model_state_hash,
            int(sample_count),
            int(sequence_number),
            base64.b64encode(nonce).decode("ascii"),
            hashlib.sha256(payload).hexdigest(),
        )

    def canonical(self) -> bytes:
        return json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()


def tag(key: bytes, envelope: Envelope) -> bytes:
    if len(key) != 32:
        raise ValueError("HMAC key must be 256 bits")

    return hmac.new(
        key,
        envelope.canonical(),
        hashlib.sha256,
    ).digest()


class ReplayState:
    def __init__(self):
        self.highest: dict[tuple[str, str], int] = {}
        self.nonces: set[tuple[str, str, str]] = set()

    def verify(
        self,
        key: bytes,
        envelope: Envelope,
        received_tag: bytes,
        payload: bytes,
        expected: dict,
    ) -> None:
        if len(received_tag) != 32 or not hmac.compare_digest(
            tag(key, envelope),
            received_tag,
        ):
            raise ValueError("invalid tag")

        if envelope.payload_hash != hashlib.sha256(payload).hexdigest():
            raise ValueError("payload modification")

        for field, value in expected.items():
            if getattr(envelope, field) != value:
                raise ValueError(f"wrong context: {field}")

        scope = (
            envelope.task_id,
            envelope.executor_id,
        )
        nonce_scope = (
            *scope,
            envelope.nonce,
        )

        if nonce_scope in self.nonces:
            raise ValueError("exact envelope replay")

        if envelope.sequence_number <= self.highest.get(scope, -1):
            raise ValueError("stale sequence number")

        self.nonces.add(nonce_scope)
        self.highest[scope] = envelope.sequence_number

    def serialized_size(self) -> int:
        return len(
            json.dumps(
                {
                    "highest": {
                        "|".join(k): v
                        for k, v in self.highest.items()
                    },
                    "nonces": sorted(
                        "|".join(k)
                        for k in self.nonces
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )