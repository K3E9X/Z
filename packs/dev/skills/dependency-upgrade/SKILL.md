---
name: dependency-upgrade
description: Upgrade one dependency in a repository and prove it still builds.
version: 1.0.0
---

1. Find every place the dependency is declared (pom.xml, build.gradle, package.json, requirements*.txt, go.mod…).
2. Change the version to the target. Do not touch unrelated dependencies.
3. Run the build and the tests with the project's own command (look at CI config first).
4. If tests fail, read the error. Fix only what the upgrade broke. If the failure is unrelated (external service, flaky test), stop and ask_human.
5. Produce `diff.patch` and `report.md` (what changed, test results, risks).
