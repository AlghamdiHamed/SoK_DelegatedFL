#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import signal
import socket
import threading
import time
from pathlib import Path

import numpy as np
import torch

from study2.attacks.work_evasion import partial_indices, substitution_permutation
from study2.common.auth import Envelope, tag
from study2.common.models import arrays_from_state, load_arrays, segments
from study2.common.resources import Monitor
from study2.common.wire import (
    Header,
    MessageLogger,
    decode_arrays,
    encode_arrays,
    pack_meta,
    receive,
    send,
    unpack_meta,
)


class Delegate:
    def __init__(self, args):
        self.args = args
        self.lock = threading.Lock()
        self.middle = None
        self.optimizer = None
        self.configuration = None
        self.contexts = {}
        self.previous = None
        self.sequence = 0

        self.key = Path(args.hmac_key).read_bytes() if args.hmac_key else None

        if self.key is not None and len(self.key) != 32:
            raise ValueError("HMAC key must be exactly 32 bytes")

        self.logger = MessageLogger(args.run_dir / "messages_E.jsonl")
        self.views = args.run_dir / "executor_views"
        self.views.mkdir(mode=0o700)
        self.metrics = (
            args.run_dir / "executor_batches.jsonl"
        ).open("x", encoding="utf-8")

    def save_view(self, name, **arrays):
        path = self.views / name

        with path.open("xb") as f:
            np.savez(f, **arrays)

    def reply(self, conn, req, kind, payload):
        h = Header.make(
            "E",
            req.sender,
            req.task_id,
            req.client_id,
            req.round_id,
            kind,
            payload,
        )
        send(conn, h, payload, self.logger)

    def handle(self, conn):
        try:
            req, payload, _ = receive(conn, self.logger)
            meta, binary = unpack_meta(payload)

            with self.lock:
                if req.message_type == "SET_SEGMENT":
                    _, middle, _, _ = segments(meta["configuration"])
                    load_arrays(middle, decode_arrays(binary))
                    self.middle = middle
                    self.configuration = meta["configuration"]

                    self.optimizer = torch.optim.SGD(
                        self.middle.parameters(),
                        lr=float(meta["learning_rate"]),
                    )
                    self.contexts.clear()

                    out = pack_meta({"status": "ready"}, b"")
                    self.reply(
                        conn,
                        req,
                        "SEGMENT_READY",
                        out,
                    )

                elif req.message_type == "SPLIT_FORWARD":
                    self.forward(
                        conn,
                        req,
                        meta,
                        binary,
                    )

                elif req.message_type == "SPLIT_BACKWARD":
                    self.backward(
                        conn,
                        req,
                        meta,
                        binary,
                    )

                elif req.message_type == "CONVENTIONAL_STEP":
                    self.conventional(
                        conn,
                        req,
                        meta,
                        binary,
                    )

                elif req.message_type == "GET_SEGMENT":
                    self.get_segment(
                        conn,
                        req,
                        meta,
                    )

                else:
                    raise ValueError(
                        f"unsupported {req.message_type}"
                    )

        except Exception as exc:
            try:
                self.reply(
                    conn,
                    req,
                    "ERROR",
                    pack_meta(
                        {"error": repr(exc)},
                        b"",
                    ),
                )
            except Exception:
                pass

        finally:
            conn.close()

    def forward(self, conn, req, meta, binary):
        if self.middle is None:
            raise RuntimeError("segment not set")

        arr = decode_arrays(binary)
        z_np = arr["z"].astype(
            np.float32,
            copy=False,
        )
        mode = meta.get("work_mode", "honest")
        start = time.thread_time_ns()
        idx = None

        if mode.startswith("partial_"):
            fraction = float(mode.split("_", 1)[1])
            idx = partial_indices(
                len(z_np),
                fraction,
            )
            z = torch.from_numpy(
                z_np[idx]
            ).requires_grad_(True)
            h = self.middle(z)

            returned = np.zeros(
                (len(z_np), h.shape[1]),
                np.float32,
            )
            returned[idx] = h.detach().numpy()

        elif mode == "stale_work":
            if (
                self.previous is None
                or self.previous.shape[0] != len(z_np)
            ):
                z = torch.from_numpy(
                    z_np
                ).requires_grad_(True)
                h = self.middle(z)
                returned = h.detach().numpy()
                mode = "stale_prime_honest"
            else:
                z = None
                h = None
                returned = self.previous.copy()

        else:
            perm = (
                substitution_permutation(
                    len(z_np),
                    int(meta["manipulation_seed"]),
                )
                if mode == "sample_substitution"
                else np.arange(len(z_np))
            )

            z = torch.from_numpy(
                z_np[perm]
            ).requires_grad_(True)
            h = self.middle(z)
            returned = h.detach().numpy()

        compute = time.thread_time_ns() - start
        self.previous = returned.copy()
        key = str(meta["batch_id"])

        self.contexts[key] = {
            "z": z,
            "h": h,
            "idx": idx,
            "mode": mode,
            "z_visible": z_np,
            "handles": np.asarray(
                meta["sample_handles"],
                dtype="U64",
            ),
            "compute_ns": compute,
            "probe_partition": meta.get(
                "probe_partition"
            ),
        }

        out = pack_meta(
            {
                "batch_id": key,
                "executor_active_compute_ns": compute,
                "sample_count": len(z_np),
            },
            encode_arrays({
                "h": returned,
            }),
        )

        self.reply(
            conn,
            req,
            "SPLIT_ACTIVATION",
            out,
        )

    def backward(self, conn, req, meta, binary):
        key = str(meta["batch_id"])
        ctx = self.contexts.pop(key)

        grad = decode_arrays(binary)["grad_h"].astype(
            np.float32,
            copy=False,
        )
        start = time.thread_time_ns()

        if ctx["mode"] == "stale_work":
            grad_z = np.zeros_like(
                ctx["z_visible"]
            )

        else:
            self.optimizer.zero_grad(
                set_to_none=True
            )

            selected = (
                grad
                if ctx["idx"] is None
                else grad[ctx["idx"]]
            )

            ctx["h"].backward(
                torch.from_numpy(selected)
            )

            if not ctx["probe_partition"]:
                self.optimizer.step()

            part = ctx["z"].grad.detach().numpy()
            grad_z = np.zeros_like(
                ctx["z_visible"]
            )
            grad_z[:] = (
                part if ctx["idx"] is None else 0
            )

            if ctx["idx"] is not None:
                grad_z[ctx["idx"]] = part

            if ctx["mode"] == "sign_modification":
                grad_z = -grad_z

        compute = (
            time.thread_time_ns()
            - start
            + ctx["compute_ns"]
        )

        if req.round_id in (1, 5, 10):
            self.save_view(
                (
                    f"round_{req.round_id:02d}_"
                    f"{ctx['probe_partition'] or 'learning'}_"
                    f"batch_{key}.npz"
                ),
                z=ctx["z_visible"],
                g=grad_z,
                sample_handles=ctx["handles"],
            )

        self.metrics.write(
            json.dumps(
                {
                    "round_id": req.round_id,
                    "batch_id": key,
                    "work_mode": ctx["mode"],
                    "probe_partition": ctx[
                        "probe_partition"
                    ],
                    "executor_active_compute_ns": compute,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        self.metrics.flush()

        out = pack_meta(
            {
                "batch_id": key,
                "executor_active_compute_ns": compute,
            },
            encode_arrays({
                "grad_z": grad_z,
            }),
        )

        self.reply(
            conn,
            req,
            "CUT_GRADIENT",
            out,
        )

    def conventional(self, conn, req, meta, binary):
        arr = decode_arrays(binary)

        z = torch.from_numpy(
            arr["z"].astype(np.float32)
        ).requires_grad_(True)

        y = torch.from_numpy(
            arr["y"].astype(np.int64)
        )

        start = time.thread_time_ns()

        self.optimizer.zero_grad(
            set_to_none=True
        )
        logits = self.middle(z)
        loss = torch.nn.functional.cross_entropy(
            logits,
            y,
        )
        loss.backward()

        if not meta.get("probe_partition"):
            self.optimizer.step()

        compute = time.thread_time_ns() - start

        if req.round_id in (1, 5, 10):
            self.save_view(
                (
                    f"round_{req.round_id:02d}_"
                    f"{meta.get('probe_partition') or 'learning'}_"
                    f"batch_{meta['batch_id']}.npz"
                ),
                z=arr["z"],
                g=z.grad.detach().numpy(),
                sample_handles=np.asarray(
                    meta["sample_handles"],
                    dtype="U64",
                ),
                labels=y.numpy(),
            )

        self.metrics.write(
            json.dumps(
                {
                    "round_id": req.round_id,
                    "batch_id": meta["batch_id"],
                    "work_mode": "honest",
                    "probe_partition": meta.get(
                        "probe_partition"
                    ),
                    "executor_active_compute_ns": compute,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        )
        self.metrics.flush()

        out = pack_meta(
            {
                "loss": float(loss.detach()),
                "executor_active_compute_ns": compute,
            },
            encode_arrays({
                "grad_z": z.grad.detach().numpy(),
            }),
        )

        self.reply(
            conn,
            req,
            "CUT_GRADIENT",
            out,
        )

    def get_segment(self, conn, req, meta):
        binary = encode_arrays(
            arrays_from_state(self.middle)
        )

        response_meta = {
            "configuration": self.configuration,
            "sequence_number": self.sequence,
        }

        if self.configuration == "II-U-H":
            if self.key is None:
                raise ValueError(
                    "II-U-H requires --hmac-key"
                )

            env = Envelope.make(
                owner_id=req.client_id,
                executor_id="E",
                task_id=req.task_id,
                round_id=req.round_id,
                batch_id=str(meta["batch_id"]),
                cut_id=meta["cut_id"],
                model_state_hash=meta[
                    "model_state_hash"
                ],
                sample_count=int(
                    meta["sample_count"]
                ),
                sequence_number=self.sequence,
                payload=binary,
            )

            response_meta.update({
                "envelope": env.__dict__,
                "tag": tag(
                    self.key,
                    env,
                ).hex(),
            })
            self.sequence += 1

        self.reply(
            conn,
            req,
            "SEGMENT_RESPONSE",
            pack_meta(
                response_meta,
                binary,
            ),
        )


def main():
    process_start = time.perf_counter_ns()

    p = argparse.ArgumentParser()
    p.add_argument(
        "--listen",
        default="0.0.0.0:7001",
    )
    p.add_argument(
        "--run-dir",
        type=Path,
        required=True,
    )
    p.add_argument("--hmac-key")

    a = p.parse_args()
    host, port = a.listen.rsplit(":", 1)

    with Monitor(
        a.run_dir / "resources_E.json"
    ):
        server = Delegate(a)

        with socket.create_server(
            (host, int(port)),
            reuse_port=False,
        ) as s:
            with (
                a.run_dir / "cold_start_E.json"
            ).open("x", encoding="utf-8") as f:
                json.dump(
                    {
                        "boundary": (
                            "module_main_entry_to_listening_socket"
                        ),
                        "cold_start_ns": (
                            time.perf_counter_ns()
                            - process_start
                        ),
                    },
                    f,
                    sort_keys=True,
                )
                f.write("\n")

            stop = threading.Event()
            workers = []

            def shutdown(_signum, _frame):
                stop.set()

                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

                s.close()

            signal.signal(
                signal.SIGTERM,
                shutdown,
            )
            signal.signal(
                signal.SIGINT,
                shutdown,
            )

            while not stop.is_set():
                try:
                    conn, _ = s.accept()
                except OSError:
                    if stop.is_set():
                        break
                    raise

                worker = threading.Thread(
                    target=server.handle,
                    args=(conn,),
                )
                worker.start()
                workers.append(worker)

            for worker in workers:
                worker.join()

    server.logger.close()
    server.metrics.close()


if __name__ == "__main__":
    main()