# 13 — CI/CD

Two equivalent pipelines run the same stages with the same open-source tools:
- GitHub Actions in `.github/workflows/` (the repo lives on GitHub): build, scan, sign and publish to GHCR;
- `.gitlab-ci.yml` at the root for the internal GitLab mirror, which also **deploys to the team's k3s**.

Both run the same stages with the same open-source tools, so results match.

## 1. Stages
| Stage | What | Blocks merge when |
|---|---|---|
| **lint** | `ruff`, `mypy --strict` (server, runner, cli), `eslint`, `tsc --noEmit`, `prettier --check`, `hadolint` (Dockerfiles), `yamllint`, `actionlint` (GitHub) | any error |
| **test** | `pytest` with coverage (server, runner, cli), UI unit and component tests, contract tests (examples validate, OpenAPI diff vs `main` must be additive unless labelled `breaking`), pack lint, RBAC matrix and route-permission tests, audit-chain test | any failure; coverage < 80 % on `server/` and `runner/` |
| **security** | `gitleaks` (secrets), `semgrep` (SAST: Python, TS, Dockerfile, Kubernetes rulesets) and `bandit`, `osv-scanner` + `pip-audit` + `pnpm audit` (dependencies), `trivy fs` and `trivy config` (repo, IaC), license check (allowlist) | any secret; high/critical findings without an accepted exception in `security/exceptions.yaml` (owner + expiry): osv-scanner and trivy fs block on CVSS >= 7 or an unscored finding, pip-audit on any vulnerability with a fix (`ci/scripts/vuln_gate.py`) |
| **build** | Multi-arch images (amd64, arm64) for `z-server`, `z-runner`, `z-browser`, `z-ui` with BuildKit (GitHub) or rootless Buildah/Kaniko (GitLab, no privileged Docker-in-Docker); reproducible tags `sha-<short>`, `vX.Y.Z` | build failure |
| **scan** | `trivy image` on each image, `dockle` (image best practices) | critical vulnerability with a fix available |
| **sbom and sign** | `syft` SBOM (SPDX JSON) attached as an attestation; `cosign` signature (keyless with GitHub OIDC; key in GitLab protected variable or Vault for the mirror); SLSA provenance via BuildKit | — (runs on main and tags only) |
| **chart** | `helm lint`, `helm template` with `values-k3s.yaml` and `values-k8s.yaml` piped to `kubeconform -strict`, `kube-score` (report only) and `kubescape` (NSA/CIS frameworks) on rendered manifests, `helm package` | lint/schema errors; kubescape score under 85 % |
| **e2e** | Compose stack with fake LLM, fake tools and fake GitLab OIDC; `tests/e2e/` scenarios of BUILD phase C; smoke install on **kind** (Kubernetes) and **k3d** (k3s) | any failure |
| **dast** | OWASP ZAP baseline scan against the running compose UI and API (authenticated with a test session) | high alerts |
| **deploy** (GitLab only) | `helm upgrade --install --atomic --wait` to the k3s `staging` namespace on `main`; to `production` on a signed tag, **manual job in a protected environment** with required approval; image signatures verified before deploy (cosign verify, or Kyverno/sigstore policy-controller in the cluster) | — |
| **release** | Changelog from conventional commits, GitHub/GitLab release with images digests, chart, SBOMs | — |

## 2. Rules
- Every merge to `main` through a PR/MR with: green pipeline, one review (two for `docs/07-security.md`, `docs/12-*`, `contracts/`, `deploy/helm/`), CODEOWNERS on those paths.
- Branch protection: no force-push on `main`, signed commits recommended, linear history.
- Actions and images pinned by digest/SHA, updated by Renovate; workflows use least-privilege `permissions:` (default `contents: read`).
- CI never has production secrets. Deploy credentials live only in the protected GitLab environment (GitLab agent for Kubernetes preferred over a kubeconfig variable).
- Test data is synthetic; no real tickets, findings or mails in fixtures.
- Security exceptions are files in the repo (`security/exceptions.yaml`: id, reason, owner, expiry), reviewed like code; expired exceptions fail the pipeline.

## 3. Supply chain lessons (March 2026)
In March 2026 a credential-stealing campaign overwrote `aquasecurity/trivy-action` and `setup-trivy` tags, shipped malicious Trivy 0.69.4–0.69.6, then published backdoored **LiteLLM 1.82.7 and 1.82.8** on PyPI (sources: Datadog Security Labs, Snyk, Kaspersky). Z uses both, so the pipeline is built to survive the next one:
- **No third-party action by tag.** Every `uses:` is pinned to a commit SHA (Renovate `helpers:pinGitHubActionDigests`), `zizmor` fails on unpinned or over-privileged workflows. Scanners are installed as binaries with pinned versions and verified checksums/signatures, not through wrapper actions.
- **Minimal CI secrets.** Default `permissions: contents: read`; `persist-credentials: false`; publishing only from `main`/tags; OIDC (keyless) instead of long-lived tokens where possible. A compromised scanner in a PR job finds nothing worth stealing.
- **Release age gate.** Renovate waits 7 days before proposing a new version (14 for LiteLLM), and dependency installs go through the internal PyPI/npm mirror.
- **LiteLLM**: run the official proxy image **pinned by digest**, never `pip install litellm` at runtime; versions 1.82.7 and 1.82.8 are blocked in `security/blocked-versions.yaml`, checked in CI.
- **Image signatures verified at deploy** (cosign verify in the deploy job, and optionally Kyverno or sigstore policy-controller in the cluster).

## 4. Files
| File | Purpose |
|---|---|
| `.github/workflows/ci.yml` | guard (blocked versions, expired exceptions), lint, Python, UI and security jobs on PRs, pushes and weekly |
| `.github/workflows/images.yml` | build and scan on PRs; on main and tags: multi-arch publish to GHCR, scan, cosign keyless signature and SPDX attestation (separate jobs and permissions) |
| `.github/workflows/helm.yml`, `chart-policy.yml` | chart lint and render with kubeconform; kubescape and kube-score |
| `.github/workflows/e2e.yml` | compose e2e on PRs touching server, runner, browser, deploy or the lockfile |
| `.gitlab-ci.yml` | all stages for the internal mirror (rootless Buildah, cosign key pair), staging deploy on main, production on a signed tag (manual, protected) |
| `ci/scripts/install-scanners.sh`, `versions.lock`, `requirements/` | scanners from pinned versions with verified checksums or hashes, fail closed |
| `ci/scripts/check_blocked.py`, `check_exceptions.py`, `vuln_gate.py`, `verify-signatures.sh` | blocked versions, exception expiry, severity gate, signature and SBOM verification before deploy |
| `security/blocked-versions.yaml`, `security/exceptions.yaml`, `security/gitleaksignore` | versions CI refuses, accepted findings (owner + expiry), reviewed gitleaks fingerprints |

The license check runs in the python and ui jobs, on what is really installed (`ci/scripts/check_licenses.py`,
policy `security/licenses.yaml`: permissive and weak-copyleft allowed, strong copyleft and source-available denied,
unknown metadata reviewed per package). Its first run excluded a closed `lmnr-claude-code-proxy` binary pulled in by
openhands-sdk (`tool.uv.exclude-dependencies`).

The `kind` job of the e2e workflow installs the chart (values-k3s + `ci/kind/values-smoke.yaml`) with the images
of the commit on a kind cluster whose `z` namespace enforces Pod Security `restricted`, then checks `/readyz`, the
dev sign-in and the seeded playbooks.

Release notes: the GitLab `release:notes` job runs `ci/scripts/changelog.py` (conventional commits since the previous
`vX.Y.Z` tag, grouped into breaking changes, features, fixes and maintenance) and the release uses that file.

ZAP baseline: the compose e2e job ends with a passive scan of the web app (`zap-baseline.py`, ZAP pinned by
digest); missing security headers or cookie flags fail the job (`security/zap-baseline.conf`), everything else is a
warning in the uploaded report.

## 5. What the repository owner sets up
**GitHub**
- Allow Actions to create GHCR packages (`packages: write`); for a private repo decide whether packages are public or deploys get a pull secret.
- Protect `main` and `v*` tags; required checks: `guard`, `lint`, `python`, `ui`, `security`, plus image builds and e2e when they run.
- Point the chart's image repositories (`server`, `runners`, `ui`, `browser`) to `ghcr.io/k3e9x/*` or the GitLab registry.

**GitLab (internal mirror)**
- Protected variables: `COSIGN_PRIVATE_KEY` (file), `COSIGN_PASSWORD` (masked), `COSIGN_PUBLIC_KEY` (file), `KUBE_CONTEXT_STAGING`, `KUBE_CONTEXT_PRODUCTION` (GitLab agent for Kubernetes contexts), `VALUES_STAGING`, `VALUES_PRODUCTION` (files: a separate runners namespace for staging if both share a cluster, and an image pull secret), `RELEASE_ALLOWED_SIGNERS` (SSH keys allowed to sign release tags).
- Optional: internal PyPI, npm and Debian mirrors, a scanner download mirror (`SCANNERS_MIRROR`), a trivy database mirror, `COSIGN_TLOG=true` if signatures may go to the public transparency log.
- Protect the `staging` and `production` environments (approval on production) and `v*` tags.
- Runners: an unprivileged runner where Buildah's chroot isolation works; qemu binfmt (flag F) for arm64 or `BUILD_PLATFORMS=linux/amd64`; a runner tagged `z-e2e` with Docker and python3 for the compose e2e.
