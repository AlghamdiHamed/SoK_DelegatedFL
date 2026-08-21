import socket
import tempfile
import unittest
from pathlib import Path

import numpy as np

from study2.common.wire import (
    Header,
    decode_arrays,
    encode_arrays,
    receive,
    send,
)


class WireTests(unittest.TestCase):
    def test_frame_and_binary_arrays(self):
        a, b = socket.socketpair()

        payload = encode_arrays(
            {
                "z": np.arange(
                    12,
                    dtype=np.float32,
                ).reshape(3, 4)
            }
        )

        h = Header.make(
            "O_1",
            "E",
            "t",
            "O_1",
            1,
            "Z",
            payload,
        )

        sent = send(a, h, payload)
        rh, rp, got = receive(b)

        a.close()
        b.close()

        self.assertEqual(sent, got)
        self.assertEqual(
            sent,
            8 + 4 + len(h.bytes()) + len(payload),
        )

        np.testing.assert_array_equal(
            decode_arrays(rp)["z"],
            np.arange(
                12,
                dtype=np.float32,
            ).reshape(3, 4),
        )


if __name__ == "__main__":
    unittest.main()