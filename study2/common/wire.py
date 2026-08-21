"""Length-prefixed canonical headers and deterministic binary NumPy payloads."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import numpy as np


VERSION = "dfl-exp/1"
MAX_FRAME = 1 << 30


@dataclass(frozen=True)
class Header:
    protocol_version: str
    sender: str
    receiver: str
    task_id: str
    client_id: str
    round_id: int
    message_type: str
    payload_bytes: int
    payload_sha256: str
    timestamp_utc: str

    @classmethod
    def make(
        cls,
        sender,
        receiver,
        task_id,
        client_id,
        round_id,
        message_type,
        payload,
    ):
        return cls(
            VERSION,
            sender,
            receiver,
            task_id,
            client_id,
            int(round_id),
            message_type,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
            datetime.now(timezone.utc).isoformat(),
        )

    def bytes(self) -> bytes:
        return json.dumps(
            asdict(self),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")


class MessageLogger:
    def __init__(self, path: Path):
        self._f = path.open("x", encoding="utf-8")
        self._lock = threading.Lock()

    def log(
        self,
        header: Header,
        direction: str,
        serialized: int,
        socket_bytes: int,
    ):
        row = {
            **asdict(header),
            "direction": direction,
            "serialized_bytes": serialized,
            "socket_bytes": socket_bytes,
        }

        with self._lock:
            self._f.write(
                json.dumps(
                    row,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            )
            self._f.flush()
            os.fsync(self._f.fileno())

    def close(self):
        self._f.close()


def _send_all(sock: socket.socket, data: bytes) -> int:
    view, total = memoryview(data), 0

    while total < len(data):
        n = sock.send(view[total:])

        if n == 0:
            raise ConnectionError("socket closed while sending")

        total += n

    return total


def _recv_exact(
    sock: socket.socket,
    n: int,
) -> tuple[bytes, int]:
    chunks, total = [], 0

    while total < n:
        b = sock.recv(n - total)

        if not b:
            raise EOFError("socket closed while receiving")

        chunks.append(b)
        total += len(b)

    return b"".join(chunks), total


def send(
    sock: socket.socket,
    header: Header,
    payload: bytes,
    logger: MessageLogger | None = None,
) -> int:
    if (
        header.protocol_version != VERSION
        or header.payload_bytes != len(payload)
        or header.payload_sha256
        != hashlib.sha256(payload).hexdigest()
    ):
        raise ValueError("header/payload mismatch")

    hb = header.bytes()
    body = struct.pack(">I", len(hb)) + hb + payload

    if len(body) > MAX_FRAME:
        raise ValueError("frame too large")

    wire = struct.pack(">Q", len(body)) + body
    actual = _send_all(sock, wire)

    if logger:
        logger.log(
            header,
            "sent",
            len(wire),
            actual,
        )

    return actual


def receive(
    sock: socket.socket,
    logger: MessageLogger | None = None,
) -> tuple[Header, bytes, int]:
    prefix, actual = _recv_exact(sock, 8)
    size = struct.unpack(">Q", prefix)[0]

    if size < 4 or size > MAX_FRAME:
        raise ValueError("invalid frame size")

    body, n = _recv_exact(sock, size)
    actual += n
    hlen = struct.unpack(">I", body[:4])[0]

    if hlen < 2 or 4 + hlen > len(body):
        raise ValueError("invalid header length")

    raw = json.loads(body[4 : 4 + hlen])
    header = Header(**raw)
    payload = body[4 + hlen :]

    if (
        header.protocol_version != VERSION
        or header.payload_bytes != len(payload)
        or header.payload_sha256
        != hashlib.sha256(payload).hexdigest()
    ):
        raise ValueError("invalid header or digest")

    if logger:
        logger.log(
            header,
            "received",
            8 + size,
            actual,
        )

    return header, payload, actual


def rpc(
    address: tuple[str, int],
    header: Header,
    payload: bytes,
    logger=None,
    timeout=600,
):
    with socket.create_connection(address, timeout=10) as s:
        s.settimeout(timeout)
        s.setsockopt(
            socket.IPPROTO_TCP,
            socket.TCP_NODELAY,
            1,
        )

        send(
            s,
            header,
            payload,
            logger,
        )
        return receive(s, logger)


def encode_arrays(
    arrays: Mapping[str, np.ndarray],
) -> bytes:
    parts = [
        b"DFLARR1\0",
        struct.pack(">I", len(arrays)),
    ]

    for name in sorted(arrays):
        a = np.ascontiguousarray(arrays[name])
        nb = name.encode("utf-8")
        db = a.dtype.str.encode("ascii")

        if a.dtype.kind not in "biufc":
            raise TypeError("only numeric tensors are allowed")

        parts += [
            struct.pack(">H", len(nb)),
            nb,
            struct.pack(">H", len(db)),
            db,
            struct.pack(">B", a.ndim),
        ]

        parts += [
            struct.pack(">Q", int(x))
            for x in a.shape
        ]

        raw = a.tobytes(order="C")
        parts += [
            struct.pack(">Q", len(raw)),
            raw,
        ]

    return b"".join(parts)


def decode_arrays(
    data: bytes,
) -> dict[str, np.ndarray]:
    if not data.startswith(b"DFLARR1\0"):
        raise ValueError("array magic")

    o = 8
    count = struct.unpack_from(">I", data, o)[0]
    o += 4
    out = {}

    for _ in range(count):
        n = struct.unpack_from(">H", data, o)[0]
        o += 2
        name = data[o : o + n].decode()
        o += n

        n = struct.unpack_from(">H", data, o)[0]
        o += 2
        dtype = np.dtype(
            data[o : o + n].decode()
        )
        o += n

        ndim = data[o]
        o += 1

        shape = tuple(
            struct.unpack_from(
                ">Q",
                data,
                o + 8 * i,
            )[0]
            for i in range(ndim)
        )
        o += 8 * ndim

        size = struct.unpack_from(
            ">Q",
            data,
            o,
        )[0]
        o += 8

        raw = data[o : o + size]
        o += size

        if (
            name in out
            or dtype.kind not in "biufc"
            or int(
                np.prod(
                    shape,
                    dtype=np.int64,
                )
            )
            * dtype.itemsize
            != size
        ):
            raise ValueError("invalid array record")

        out[name] = (
            np.frombuffer(
                raw,
                dtype=dtype,
            )
            .reshape(shape)
            .copy()
        )

    if o != len(data):
        raise ValueError("trailing array bytes")

    return out


def pack_meta(
    meta: dict,
    binary: bytes,
) -> bytes:
    m = json.dumps(
        meta,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()

    return (
        struct.pack(">I", len(m))
        + m
        + binary
    )


def unpack_meta(
    payload: bytes,
) -> tuple[dict, bytes]:
    if len(payload) < 4:
        raise ValueError("short metadata")

    n = struct.unpack(
        ">I",
        payload[:4],
    )[0]

    if n < 2 or 4 + n > len(payload):
        raise ValueError("metadata length")

    return (
        json.loads(payload[4 : 4 + n]),
        payload[4 + n :],
    )