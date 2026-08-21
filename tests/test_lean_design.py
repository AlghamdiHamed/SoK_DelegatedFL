import csv
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class LeanDesignTests(unittest.TestCase):
    def test_rounds_and_privacy_checkpoints(self):
        study1 = yaml.safe_load((ROOT / "configs/study1.yaml").read_text())
        study2 = yaml.safe_load((ROOT / "configs/study2.yaml").read_text())
        self.assertEqual(study1["model"]["rounds"], 10)
        self.assertEqual(study1["linked_round_counts"], [1, 5, 10])
        self.assertEqual(study2["model"]["rounds"], 10)
        self.assertEqual(study2["privacy_rounds"], [1, 5, 10])
        self.assertEqual(study2["cut_comparison_primary_privacy_round"], 10)

    def test_three_seed_three_repetition_budget(self):
        seeds = yaml.safe_load((ROOT / "configs/seeds.yaml").read_text())
        hardware = yaml.safe_load((ROOT / "configs/hardware.yaml").read_text())
        self.assertEqual(seeds["learning"], [101, 211, 307])
        self.assertEqual(seeds["systems_repetitions"], 3)
        self.assertEqual(seeds["systems_warmups"], 2)
        self.assertEqual(hardware["measurement"]["repetitions"], 3)

    def test_schedule_has_three_complete_blocks(self):
        with (ROOT / "configs/systems_schedule.csv").open(newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 27)
        expected = {
            "study1": {"I-L", "I-P", "I-OR", "I-ER"},
            "study2": {"II-L", "II-S", "II-U-E", "II-U-D", "II-U-H"},
        }
        for study, configurations in expected.items():
            for block in (1, 2, 3):
                observed = {
                    row["configuration"]
                    for row in rows
                    if row["study"] == study and int(row["block"]) == block
                }
                self.assertEqual(observed, configurations)


if __name__ == "__main__":
    unittest.main()
