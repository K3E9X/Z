import unittest

from sync import members_to_add, members_to_remove


class SyncTest(unittest.TestCase):
    def test_diff(self) -> None:
        directory, team = {"amartin", "lpetit"}, {"lpetit", "old.user"}
        self.assertEqual(members_to_add(directory, team), ["amartin"])
        self.assertEqual(members_to_remove(directory, team), ["old.user"])


if __name__ == "__main__":
    unittest.main()
