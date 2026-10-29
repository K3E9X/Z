"""Integration test: needs the staging directory ldap-staging.example.internal (port 636), not reachable from CI
runners outside the admin network. It has failed on main since the network change of 2026-10-02."""

import socket
import unittest


class LdapIntegrationTest(unittest.TestCase):
    def test_directory_reachable(self) -> None:
        with socket.create_connection(("ldap-staging.example.internal", 636), timeout=5):
            pass


if __name__ == "__main__":
    unittest.main()
