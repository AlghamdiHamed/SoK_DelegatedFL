import unittest

from study2.common.auth import Envelope, ReplayState, tag


class HMACTests(unittest.TestCase):
    def setUp(self):
        self.key = b"k" * 32
        self.payload = b"payload"
        self.kw = dict(
            owner_id="O_1",
            executor_id="E",
            task_id="study2",
            round_id=1,
            batch_id="b",
            cut_id="early",
            model_state_hash="a" * 64,
            sample_count=4,
            sequence_number=1,
            payload=self.payload,
            nonce=b"n" * 16,
        )
        self.expected = {
            k: self.kw[k]
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
        self.expected["protocol_version"] = "dfl-exp/1"

    def test_accept_then_replay_rejected(self):
        e = Envelope.make(**self.kw)
        s = ReplayState()
        s.verify(
            self.key,
            e,
            tag(self.key, e),
            self.payload,
            self.expected,
        )

        with self.assertRaisesRegex(ValueError, "replay"):
            s.verify(
                self.key,
                e,
                tag(self.key, e),
                self.payload,
                self.expected,
            )

    def test_context_and_payload_controls(self):
        e = Envelope.make(**self.kw)

        with self.assertRaises(ValueError):
            ReplayState().verify(
                self.key,
                e,
                tag(self.key, e),
                b"changed",
                self.expected,
            )

        bad = {**self.expected, "round_id": 2}

        with self.assertRaisesRegex(ValueError, "round_id"):
            ReplayState().verify(
                self.key,
                e,
                tag(self.key, e),
                self.payload,
                bad,
            )

    def test_tag_control(self):
        e = Envelope.make(**self.kw)
        t = tag(self.key, e)

        with self.assertRaisesRegex(ValueError, "tag"):
            ReplayState().verify(
                self.key,
                e,
                t[:-1] + b"x",
                self.payload,
                self.expected,
            )


if __name__ == "__main__":
    unittest.main()