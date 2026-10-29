import unittest

from counters import rate_bps


class RateTest(unittest.TestCase):
    def test_simple_rate(self) -> None:
        self.assertEqual(rate_bps(1_000, 2_000, 8), 1_000.0)

    def test_bad_interval(self) -> None:
        with self.assertRaises(ValueError):
            rate_bps(0, 10, 0)


if __name__ == "__main__":
    unittest.main()
