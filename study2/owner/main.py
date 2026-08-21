#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path

import numpy as np
import torch

from study2.common.auth import Envelope, ReplayState
from study2.common.data import load_frozen
from study2.common.models import (
    FullMLP,
    arrays_from_state,
    combined_state,
    load_arrays,
    segments,
    state_hash,
)
from study2.common.resources import Monitor
from study2.common.wire import (
    Header,
    MessageLogger,
    decode_arrays,
    encode_arrays,
    pack_meta,
    rpc,
    unpack_meta,
)


def address(s):
    host, port = s.rsplit(":", 1)
    return host, int(port)


class Owner:
    def __init__(self, a):
        self.a = a
        self.owner = a.owner
        self.configuration = a.configuration if a.owner == "O_1" else "II-L"
        self.log = MessageLogger(a.run_dir / f"messages_{a.owner}.jsonl")
        self.replay = ReplayState()
        self.key = Path(a.hmac_key).read_bytes() if a.hmac_key else None

        if self.configuration == "II-U-H" and (
            self.key is None or len(self.key) != 32
        ):
            raise ValueError("II-U-H needs a 32-byte --hmac-key")

        self.X, self.y, _, self.ids = load_frozen(
            a.records,
            a.owner,
            "learning",
        )

        self.probe_data = (
            {
                p: load_frozen(a.records, a.owner, p)
                for p in ("attacker_auxiliary", "attacker_target")
            }
            if a.owner == "O_1" and a.collect_privacy
            else {}
        )

        self.metrics = (
            a.run_dir / f"owner_{a.owner}_rounds.jsonl"
        ).open("x", encoding="utf-8")

    def call(self, who, kind, round_id, meta, binary=b""):
        target = self.a.executor if who == "E" else self.a.aggregator
        payload = pack_meta(meta, binary)
        h = Header.make(
            self.owner,
            who,
            self.a.task_id,
            self.owner,
            round_id,
            kind,
            payload,
        )
        rh, rp, _ = rpc(
            address(target),
            h,
            payload,
            self.log,
        )

        rmeta, rbin = unpack_meta(rp)

        if rh.message_type == "ERROR":
            raise RuntimeError(rmeta)

        return rh, rmeta, rbin

    def batches(self, round_id):
        rng = np.random.default_rng(self.a.seed + round_id)
        order = rng.permutation(len(self.X))

        for b, start in enumerate(range(0, len(order), 32)):
            idx = order[start : start + 32]
            yield (
                b,
                idx,
                torch.from_numpy(self.X[idx]),
                torch.from_numpy(self.y[idx]),
            )

    def local_epoch(self, state, round_id):
        model = FullMLP()
        load_arrays(model, state)
        opt = torch.optim.SGD(model.parameters(), lr=.02)
        active = 0
        losses = []

        for _, _, x, y in self.batches(round_id):
            t = time.thread_time_ns()
            opt.zero_grad(set_to_none=True)
            loss = torch.nn.functional.cross_entropy(model(x), y)
            loss.backward()
            opt.step()
            active += time.thread_time_ns() - t
            losses.append(float(loss.detach()))

        return (
            arrays_from_state(model),
            active,
            float(np.mean(losses)),
        )

    def split_epoch(self, state, round_id):
        head, middle, tail, cut = segments(self.configuration)
        load_arrays(head, state)
        load_arrays(middle, state)

        if tail is not None:
            load_arrays(tail, state)

        owner_params = (
            list(head.parameters())
            + ([] if tail is None else list(tail.parameters()))
        )
        opt = torch.optim.SGD(owner_params, lr=.02)

        self.call(
            "E",
            "SET_SEGMENT",
            round_id,
            {
                "configuration": self.configuration,
                "learning_rate": .02,
            },
            encode_arrays(arrays_from_state(middle)),
        )

        active = 0
        losses = []
        sample_total = 0

        for b, idx, x, y in self.batches(round_id):
            handles = self.ids[idx].tolist()

            t = time.thread_time_ns()
            opt.zero_grad(set_to_none=True)
            z = head(x)
            active += time.thread_time_ns() - t

            meta = {
                "batch_id": str(b),
                "sample_handles": handles,
                "work_mode": self.a.work_mode,
                "manipulation_seed": (
                    self.a.seed + round_id * 1000 + b
                ),
            }

            if self.configuration == "II-S":
                _, rmeta, rbin = self.call(
                    "E",
                    "CONVENTIONAL_STEP",
                    round_id,
                    meta,
                    encode_arrays({
                        "z": z.detach().numpy(),
                        "y": y.numpy(),
                    }),
                )
                grad = decode_arrays(rbin)["grad_z"]

                t = time.thread_time_ns()
                z.backward(torch.from_numpy(grad))
                opt.step()
                active += time.thread_time_ns() - t
                losses.append(float(rmeta["loss"]))
            else:
                _, _, rbin = self.call(
                    "E",
                    "SPLIT_FORWARD",
                    round_id,
                    meta,
                    encode_arrays({
                        "z": z.detach().numpy(),
                    }),
                )
                h = decode_arrays(rbin)["h"]
                h_owner = torch.from_numpy(h).requires_grad_(True)

                t = time.thread_time_ns()
                logits = tail(h_owner)
                loss = torch.nn.functional.cross_entropy(logits, y)
                loss.backward()
                grad_h = h_owner.grad.detach().numpy()
                active += time.thread_time_ns() - t

                _, _, rbin = self.call(
                    "E",
                    "SPLIT_BACKWARD",
                    round_id,
                    {"batch_id": str(b)},
                    encode_arrays({
                        "grad_h": grad_h,
                    }),
                )
                grad_z = decode_arrays(rbin)["grad_z"]

                t = time.thread_time_ns()
                z.backward(torch.from_numpy(grad_z))
                opt.step()
                active += time.thread_time_ns() - t
                losses.append(float(loss.detach()))

            sample_total += len(idx)

        if self.a.collect_privacy and round_id in (1, 5, 10):
            self.collect_privacy(
                head,
                tail,
                round_id,
            )

        global_hash = state_hash(state)

        _, rmeta, rbin = self.call(
            "E",
            "GET_SEGMENT",
            round_id,
            {
                "batch_id": "segment",
                "cut_id": cut,
                "model_state_hash": global_hash,
                "sample_count": sample_total,
            },
        )

        if self.configuration == "II-U-H":
            env = Envelope(**rmeta["envelope"])
            start = time.thread_time_ns()

            self.replay.verify(
                self.key,
                env,
                bytes.fromhex(rmeta["tag"]),
                rbin,
                {
                    "protocol_version": "dfl-exp/1",
                    "owner_id": self.owner,
                    "executor_id": "E",
                    "task_id": self.a.task_id,
                    "round_id": round_id,
                    "batch_id": "segment",
                    "cut_id": cut,
                    "model_state_hash": global_hash,
                    "sample_count": sample_total,
                },
            )

            hmac_ns = time.thread_time_ns() - start
        else:
            hmac_ns = 0

        with (
            self.a.run_dir
            / f"returned_segment_round_{round_id:02d}.bin"
        ).open("xb") as f:
            f.write(rbin)

        load_arrays(middle, decode_arrays(rbin))

        return (
            combined_state(head, middle, tail),
            active,
            float(np.mean(losses)),
            hmac_ns,
        )

    def collect_privacy(self, head, tail, round_id):
        for partition, (X, y, subject, ids) in self.probe_data.items():
            for b, start in enumerate(range(0, len(X), 32)):
                x = torch.from_numpy(X[start : start + 32])
                labels = torch.from_numpy(y[start : start + 32])
                handles = ids[start : start + 32].tolist()
                z = head(x)

                meta = {
                    "batch_id": f"probe-{partition}-{b}",
                    "sample_handles": handles,
                    "work_mode": "honest",
                    "manipulation_seed": (
                        self.a.seed + round_id * 1000 + b
                    ),
                    "probe_partition": partition,
                }

                if self.configuration == "II-S":
                    _, _, rbin = self.call(
                        "E",
                        "CONVENTIONAL_STEP",
                        round_id,
                        meta,
                        encode_arrays({
                            "z": z.detach().numpy(),
                            "y": labels.numpy(),
                        }),
                    )
                    continue

                _, _, rbin = self.call(
                    "E",
                    "SPLIT_FORWARD",
                    round_id,
                    meta,
                    encode_arrays({
                        "z": z.detach().numpy(),
                    }),
                )

                h = torch.from_numpy(
                    decode_arrays(rbin)["h"]
                ).requires_grad_(True)

                loss = torch.nn.functional.cross_entropy(
                    tail(h),
                    labels,
                )
                loss.backward()

                self.call(
                    "E",
                    "SPLIT_BACKWARD",
                    round_id,
                    {"batch_id": meta["batch_id"]},
                    encode_arrays({
                        "grad_h": h.grad.detach().numpy(),
                    }),
                )

    def run(self):
        for r in range(1, 11):
            online_start = time.perf_counter_ns()
            round_cpu_start = time.process_time_ns()

            _, meta, binary = self.call(
                "A",
                "GET_MODEL",
                r,
                {},
            )
            state = decode_arrays(binary)

            if self.configuration == "II-L":
                new, active, loss = self.local_epoch(state, r)
                hmac_ns = 0
            else:
                new, active, loss, hmac_ns = self.split_epoch(
                    state,
                    r,
                )

            pre_contribution_online = (
                time.perf_counter_ns() - online_start
            )

            _, accepted, _ = self.call(
                "A",
                "CONTRIBUTION",
                r,
                {
                    "sample_count": len(self.X),
                    "owner_protocol_online_ns": pre_contribution_online,
                },
                encode_arrays(new),
            )

            online = time.perf_counter_ns() - online_start

            row = {
                "round_id": r,
                "configuration": self.configuration,
                "work_mode": self.a.work_mode,
                "owner_active_cpu_ns": (
                    time.process_time_ns() - round_cpu_start
                ),
                "owner_learning_cpu_ns": active,
                "owner_protocol_online_ns": online,
                "training_loss": loss,
                "hmac_compute_ns": hmac_ns,
                "hmac_acceptance": (
                    self.configuration == "II-U-H"
                ),
                "hmac_key_bytes": (
                    len(self.key) if self.key else 0
                ),
                "hmac_tag_bytes": (
                    32 if self.configuration == "II-U-H" else 0
                ),
                "replay_state_bytes": (
                    self.replay.serialized_size()
                ),
            }

            self.metrics.write(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            self.metrics.flush()


def main():
    process_start = time.perf_counter_ns()

    p = argparse.ArgumentParser()
    p.add_argument(
        "--owner",
        choices=("O_1", "O_2", "O_3"),
        required=True,
    )
    p.add_argument(
        "--configuration",
        choices=(
            "II-L",
            "II-S",
            "II-U-E",
            "II-U-D",
            "II-U-H",
        ),
        required=True,
    )
    p.add_argument(
        "--records",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--executor",
        default="127.0.0.1:7001",
    )
    p.add_argument(
        "--aggregator",
        default="127.0.0.1:7002",
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        required=True,
    )
    p.add_argument(
        "--seed",
        type=int,
        required=True,
    )
    p.add_argument(
        "--task-id",
        default="study2",
    )
    p.add_argument("--hmac-key")
    p.add_argument(
        "--collect-privacy",
        action="store_true",
    )
    p.add_argument(
        "--work-mode",
        default="honest",
        choices=(
            "honest",
            "partial_0.25",
            "partial_0.5",
            "partial_0.75",
            "stale_work",
            "sign_modification",
            "sample_substitution",
        ),
    )

    a = p.parse_args()

    with Monitor(
        a.run_dir / f"resources_{a.owner}.json"
    ):
        owner = Owner(a)

        with (
            a.run_dir / f"cold_start_{a.owner}.json"
        ).open("x", encoding="utf-8") as f:
            json.dump(
                {
                    "boundary": (
                        "module_main_entry_to_owner_protocol_ready"
                    ),
                    "cold_start_ns": (
                        time.perf_counter_ns() - process_start
                    ),
                },
                f,
                sort_keys=True,
            )
            f.write("\n")

        owner.run()


if __name__ == "__main__":
    main()