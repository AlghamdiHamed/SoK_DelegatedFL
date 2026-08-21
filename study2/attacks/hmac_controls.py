#!/usr/bin/env python3
"""Run the prespecified external-adversary HMAC positive controls."""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

from study2.common.auth import Envelope, ReplayState, tag


FIELDS = (
    "run_id,seed,control,expected_accept,observed_accept,reason,"
    "hmac_compute_ns,key_bytes,tag_bytes,replay_state_bytes,gate_pass"
).split(",")


def attempt(key, env, t, payload, expected, state=None):
    state = ReplayState() if state is None else state
    start = time.thread_time_ns()

    try:
        state.verify(key, env, t, payload, expected)
        return (
            True,
            "accepted",
            time.thread_time_ns() - start,
            state,
        )
    except ValueError as e:
        return (
            False,
            str(e),
            time.thread_time_ns() - start,
            state,
        )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--key", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)

    a = p.parse_args()
    key = a.key.read_bytes()
    payload = b"delegated-segment"

    # The contextual control uses O_1's frozen Study-II learning count.
    base = dict(
        owner_id="O_1",
        executor_id="E",
        task_id="study2",
        round_id=10,
        batch_id="segment",
        cut_id="early",
        model_state_hash="a" * 64,
        sample_count=168,
        sequence_number=7,
        payload=payload,
        nonce=b"0" * 16,
    )

    expected = {
        k: base[k]
        for k in (
            "owner_id",
            "executor_id",
            "task_id",
            "round_id",
            "batch_id",
            "cut_id",
            "model_state_hash",
            "sample_count",
        )
    }
    expected["protocol_version"] = "dfl-exp/1"

    cases = []

    env = Envelope.make(**base)
    good = tag(key, env)

    cases.append(
        (
            "payload_modification",
            env,
            good,
            payload + b"!",
            expected,
            None,
        )
    )
    cases.append(
        (
            "tag_modification",
            env,
            bytes([good[0] ^ 1]) + good[1:],
            payload,
            expected,
            None,
        )
    )

    state = ReplayState()
    state.verify(key, env, good, payload, expected)
    cases.append(
        (
            "exact_replay",
            env,
            good,
            payload,
            expected,
            state,
        )
    )

    for name, field, value in (
        ("wrong_task", "task_id", "other"),
        ("wrong_round", "round_id", 11),
        ("wrong_batch", "batch_id", "other"),
        (
            "wrong_model_state_hash",
            "model_state_hash",
            "b" * 64,
        ),
    ):
        altered = {
            **base,
            field: value,
            "nonce": bytes(
                name[:16].ljust(16, "0"),
                "ascii",
            ),
        }
        e = Envelope.make(**altered)
        cases.append(
            (
                name,
                e,
                tag(key, e),
                payload,
                expected,
                None,
            )
        )

    state = ReplayState()
    first = {
        **base,
        "sequence_number": 8,
        "nonce": b"1" * 16,
    }
    e1 = Envelope.make(**first)
    state.verify(
        key,
        e1,
        tag(key, e1),
        payload,
        expected,
    )

    stale = {
        **base,
        "sequence_number": 7,
        "nonce": b"2" * 16,
    }
    es = Envelope.make(**stale)

    cases.append(
        (
            "stale_sequence",
            es,
            tag(key, es),
            payload,
            expected,
            state,
        )
    )

    with a.output.open(
        "x",
        newline="",
        encoding="utf-8",
    ) as f:
        w = csv.DictWriter(
            f,
            fieldnames=FIELDS,
        )
        w.writeheader()

        for name, e, t, b, ex, s in cases:
            accepted, reason, ns, state = attempt(
                key,
                e,
                t,
                b,
                ex,
                s,
            )

            w.writerow(
                {
                    "run_id": a.run_id,
                    "seed": a.seed,
                    "control": name,
                    "expected_accept": "false",
                    "observed_accept": str(
                        accepted
                    ).lower(),
                    "reason": reason,
                    "hmac_compute_ns": ns,
                    "key_bytes": len(key),
                    "tag_bytes": len(t),
                    "replay_state_bytes": (
                        state.serialized_size()
                    ),
                    "gate_pass": str(
                        not accepted
                    ).lower(),
                }
            )


if __name__ == "__main__":
    main()