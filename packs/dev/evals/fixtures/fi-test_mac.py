import unittest

from mac import normalize_mac


class NormalizeMacTest(unittest.TestCase):
    def test_colons(self) -> None:
        self.assertEqual(normalize_mac("AA:BB:CC:00:11:22"), "aa:bb:cc:00:11:22")

    def test_bare(self) -> None:
        self.assertEqual(normalize_mac("aabbcc001122"), "aa:bb:cc:00:11:22")

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            normalize_mac("not-a-mac")


if __name__ == "__main__":
    unittest.main()
