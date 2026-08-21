import unittest

import numpy as np

from preprocessing.pamap2 import (
    ACTIVITIES,
    Window,
    assert_no_temporal_overlap,
    partition_ranges,
)


def w(part, start, end, record):
    return Window(
        "study1",
        record,
        "O_1",
        "S1",
        1,
        0,
        part,
        start,
        end,
        float(start),
        float(end),
        1.0,
        (),
        None,
    )


class PreprocessingTests(unittest.TestCase):
    def test_protocol_activity_ids(self):
        self.assertEqual(
            ACTIVITIES,
            (1, 2, 3, 4, 5, 6, 7, 12, 13, 16, 17, 24),
        )

    def test_guards_precede_windows(self):
        r = partition_ranges(
            0,
            10000,
            {
                "learning": 0.5,
                "utility_test": 0.2,
                "attacker_auxiliary": 0.15,
                "attacker_target": 0.15,
            },
            256,
        )

        self.assertEqual(r["learning"], (0, 4744))
        self.assertEqual(r["utility_test"], (5256, 6744))
        self.assertGreater(
            r["attacker_target"][0],
            r["attacker_auxiliary"][1],
        )

    def test_overlap_gate_fails(self):
        with self.assertRaises(AssertionError):
            assert_no_temporal_overlap(
                [
                    w("learning", 0, 255, "a"),
                    w("utility_test", 128, 383, "b"),
                ]
            )

    def test_nonoverlap_passes(self):
        assert_no_temporal_overlap(
            [
                w("learning", 0, 255, "a"),
                w("utility_test", 512, 767, "b"),
            ]
        )


if __name__ == "__main__":
    unittest.main()