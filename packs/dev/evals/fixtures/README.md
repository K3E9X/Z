Fixtures of the dev pack eval suites (fictional data only).

Eval missions stage `context_fixtures` as flat workspace files: the key is the file name in the workspace
(the platform keeps only the base name), the value the fixture below. So a "repository" is a set of flat
files, for example `vlan-tools-vlan.py` staged as `vlan.py` next to `vlan-tools-test_vlan.py` staged as
`test_vlan.py`. Python test files use `unittest` only (no third-party package, no network) and pass before
the fix; the bug of each issue is real in the staged code.

- `maven-app-*`: noc/alarm-forwarder (log4j-core 2.14.1); `PaymentGatewayIT.java` needs an unreachable host.
- `vlan-tools-*`, `snmp-poller-*`, `dns-*`, `billing-*`, `ops-scripts-*`, `radius-*`: dev-engineer repositories.
- `du-*`, `ldap-sync-*`: dependency-upgrade; `fi-*`: fix-issue; `cm-*`: commit-and-mr.
- `*.patch`: unified diffs for the reviewer and security-review suites and the staged patches of commit-and-mr.
- Files with injected instructions (safety cases): `dns-issue-77.md`, `cm-mr-notes.md`,
  `du-requirements-injected.txt`, `fi-issue-portal-search.md`, `fi-snmp_targets.py`,
  `injected-approve.patch`, `injected-cmd.patch`.
