import unittest

from portal_api import search_customers


class SearchTest(unittest.TestCase):
    def test_role_required(self) -> None:
        with self.assertRaises(PermissionError):
            search_customers(set(), "paul")

    def test_ascii_query(self) -> None:
        self.assertEqual(search_customers({"support-agent"}, "martin"), ["Paul Martin"])


if __name__ == "__main__":
    unittest.main()
