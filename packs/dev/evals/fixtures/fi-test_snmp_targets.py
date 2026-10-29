import unittest

from snmp_targets import parse_targets


class ParseTargetsTest(unittest.TestCase):
    def test_lowercase_hosts(self) -> None:
        self.assertEqual(parse_targets("core-par-01\n\nolt-lyo-03\n"), ["core-par-01", "olt-lyo-03"])


if __name__ == "__main__":
    unittest.main()
