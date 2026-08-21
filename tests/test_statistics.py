import unittest

from analysis.statistics import paired_bootstrap, seed_summary, systems_summary


class StatisticsTests(unittest.TestCase):
    def test_seed_summary(self):
        self.assertEqual(seed_summary(range(3))["n"], 3)

    def test_paired(self):
        self.assertAlmostEqual(
            paired_bootstrap([2] * 3, [1] * 3, draws=1000)["paired_mean"],
            1,
        )

    def test_systems_count(self):
        with self.assertRaises(ValueError):
            systems_summary(range(2))

        self.assertEqual(systems_summary(range(3))["n"], 3)


if __name__ == "__main__":
    unittest.main()