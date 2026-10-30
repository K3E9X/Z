# ADR 0018 — CI pipelines as built

Status: accepted

## Context
docs/13 sets the stages, the tools and the supply-chain rules (pinned actions, verified scanners, release age
gate, signed images verified before deploy). It leaves the severity rule per scanner, how the GitLab mirror
builds without privileged Docker, and how signatures work on an internal mirror.

## Decision
1. **Scanners are binaries or hashed wheels**, installed by `ci/scripts/install-scanners.sh` from
   `versions.lock` (sha256 from each release's own checksum file) and `requirements/*.txt` (pip
   `--require-hashes`, wheels only). It fails closed on an unknown tool, a checksum mismatch or a blocked
   version, before downloading. No scanner wrapper action is used.
2. **Severity gate** (`vuln_gate.py`): osv-scanner and trivy fs block on CVSS >= 7 or an unscored finding;
   pip-audit has no severity, so any vulnerability with a fixed version blocks. Accepted findings live in
   `security/exceptions.yaml` with owner and expiry (at most a year); expired ones fail the guard job.
3. **Separate permissions per job** on GitHub: `publish` (packages: write) builds and pushes; `scan` (read
   only) runs trivy and dockle after the registry login is removed; `sign` (id-token: write) signs only a
   scanned digest and verifies it with the identity a deploy uses.
4. **GitLab builds with rootless Buildah** (chroot isolation, no privileged Docker-in-Docker), from Debian's
   signed package in a pinned image because quay.io was not reachable to pin the official one; override
   with `BUILDAH_IMAGE`. Images are signed with a cosign key pair from protected variables after the scan.
5. **No transparency log by default on the mirror** (`COSIGN_TLOG=false`): internal image digests stay
   internal; verification then needs `--insecure-ignore-tlog`, which `verify-signatures.sh` only uses in
   key-pair mode with `COSIGN_IGNORE_TLOG=1`. Every deploy verifies signatures and the SPDX attestation first.
6. **kube-score reports only** (the chart has findings it rates critical that are deliberate, e.g. no CPU
   limits on some pods); kubescape blocks under 85 %.
7. **Dependencies under the gate, found at merge time**: filelock moved to 4.2.4 (13 CVEs), which needs the
   litellm library at 1.103.4 (1.105.1 caps filelock below 4; the gateway proxy image is pinned separately).
   Base images were re-pinned to digests older than 7 days: `python:3.12.15-slim-bookworm`, and
   `nginx-unprivileged:1.30-alpine` (1.28 is end of life and had critical CVEs).

## Consequences
- A compromised scanner release or action tag cannot enter the pipeline unnoticed, and a job that runs
  untrusted code never holds a write or signing token.
- Scanner pins and requirement files are refreshed by hand (command in each file header) until Renovate rules
  cover them.
