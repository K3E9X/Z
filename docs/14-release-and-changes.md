# 14 — Releases and changes

How a change goes from an idea to production, and back out if it must. The pipelines are in `13-ci-cd.md`, the
targets in `11-deploy.md`, the per-environment values in `deploy/environments/`.

## 1. Environments
| Environment | What runs | Fed by | Who may deploy |
|---|---|---|---|
| development | Docker Compose, fakes, `Z_ENV=development` | any branch, on a laptop | the developer |
| staging | the k3s release `z` in `z-staging`, real GitLab, test Jira projects, the inferX project of staging | every merge to `main`, automatically | the pipeline |
| production | the k3s release `z` in `z` | a signed tag `vX.Y.Z`, manually, after approval in the protected environment | release managers (GitLab protected environment) |

Same chart and same images everywhere: only the site values differ (hosts, OIDC application, egress ranges,
secret names, budgets). Staging never holds production data, credentials or model keys.

## 2. Day to day: features and fixes
1. **An issue first** (what and why, acceptance criteria). Security-sensitive work names the invariant it touches.
2. **A short-lived branch from `main`** (`feat/…`, `fix/…`), small commits in conventional form
   (`feat(server): …`, `fix(ui): …`): the release notes are generated from them.
3. **A merge request** to `main`: the pipeline must be green (lint, types, tests, scans, chart, e2e) and one review
   is required, two for `docs/07`, `docs/12`, `contracts/` and `deploy/helm/` (CODEOWNERS).
4. **Merged into `main`** (linear history): staging is deployed automatically, then checked by the smoke list in §5.
5. **Released** when staging is good: a signed tag `vX.Y.Z` on that commit; the production deploy job waits for an
   approval in the protected environment, verifies the image signatures and SBOMs, then runs `helm upgrade --atomic`.

Unfinished work merges behind a switch instead of living in a long branch:
- new connectors and operations ship **unexposed** until an admin approves them (exposure review);
- new agents, skills and playbooks ship in a pack version and can stay out of a team's catalog;
- platform behaviour that must be switched per environment goes in `config/z.yaml` (or Helm `config.z`), never in a
  code branch. Guardrails marked `locked` cannot be switched off by anyone at runtime.

## 3. Versions
- Semantic versions on the whole platform (server, runner, UI, chart share one): `MAJOR` for a breaking change of
  the API, the pack format or an upgrade that needs manual steps; `MINOR` for features; `PATCH` for fixes.
- `contracts/` changes are additive unless the merge request is labelled `breaking`; a breaking change means a
  `MAJOR` and a migration note in the release. The pipeline does not diff the OpenAPI file yet (`13-ci-cd.md` §1):
  the two reviewers of `contracts/` check that no path, operation or required field disappears.
- Images are tagged `sha-<short>` on every build and `vX.Y.Z` on release, and always **deployed by digest**.

## 4. Database migrations
- Alembic migrations run before the new pods (Helm pre-upgrade Job `migrate`), forward only.
- **Expand, then contract**: a release only adds (columns nullable or with defaults, new tables, new indexes built
  concurrently); removing or renaming happens in a later release, once no running version reads the old shape. So
  the previous release keeps working on the new schema, which is what makes a rollback possible.
- Never rewrite the audit tables: they are append-only for the application role (invariant 9).
- Back up before every production upgrade (`deploy/README.md`, "Backups and keys"): the database, `Z_SECRET_KEY`
  and `LITELLM_SALT_KEY` together.

## 5. After every deploy
`helm --wait` only says the pods are ready. Check, in this order:
1. `GET /readyz` and the Inference screen (LiteLLM and inferX healthy, the inferX key check green).
2. Sign in with GitLab; the Admin area shows no failed checkpoint signature and audit forwarding is delivering.
3. A read-only playbook on a test project (staging) or a known-safe one (production) runs to `succeeded`.
4. Review shows nothing stuck; `/metrics` has no burst of `needs_attention` or failed jobs.

## 6. Rolling back
- **Application**: `helm rollback z <previous revision> -n z` (or re-run the production job of the previous tag).
  It works because migrations are expand-only (§4); a release whose notes say "no rollback" (a contract step) must
  be fixed forward instead.
- **Configuration**: site values live in the protected variable or the GitOps repository, versioned: revert there
  and re-run the deploy.
- **Agents, skills, routing rules, memory**: every change is versioned and reversible from the web app (the learning
  loop and the catalog keep the previous version, invariant 14); no deploy needed.
- **Data restore** is the last resort: restore the backup taken before the upgrade, with its keys.

## 7. Hotfix
1. Branch `fix/…` from `main`, fix with a test that fails before the fix, merge as usual (the pipeline is not
   skipped for a hotfix).
2. If `main` already holds work that is not ready to ship, branch `release/vX.Y` from the last tag, cherry-pick the
   fix there, tag `vX.Y.(Z+1)` from it, and keep the fix on `main` too.
3. Security fixes follow the CSIRT process of the organisation: the advisory and the rotation of any exposed
   credential come with the patch, not after it.

## 8. Who decides
| Change | Approves |
|---|---|
| Code, chart, CI | a reviewer (two on security, identity, contracts, Helm) |
| Promotion to production | a release manager, in the protected environment |
| A connector's exposed operations | an admin of Z (exposure review) |
| Agent, skill, routing or memory changes proposed by the learning loop | a human in the web app, never automatic |
| Accepted security exceptions | the owner named in `security/exceptions.yaml`, with an expiry |
