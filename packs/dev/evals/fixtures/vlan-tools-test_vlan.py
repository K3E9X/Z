import unittest

from vlan import parse_vlan_range


class ParseVlanRangeTest(unittest.TestCase):
    def test_single(self) -> None:
        self.assertEqual(parse_vlan_range("100"), [100])

    def test_list(self) -> None:
        self.assertEqual(parse_vlan_range("300, 100"), [100, 300])

    def test_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            parse_vlan_range("4095")


if __name__ == "__main__":
    unittest.main()
