import unittest

from inventory import parse_inventory


class InventoryTest(unittest.TestCase):
    def test_parses_hosts_and_skips_comments(self) -> None:
        text = "# export 2026-10-01\nbras-lyo-01;LYO3;bras\n\nolt-mrs-12;MRS1;olt\n"
        self.assertEqual(
            parse_inventory(text),
            [
                {"hostname": "bras-lyo-01", "site": "LYO3", "role": "bras"},
                {"hostname": "olt-mrs-12", "site": "MRS1", "role": "olt"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
