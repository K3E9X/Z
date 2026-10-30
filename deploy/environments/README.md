# Environments

One Helm release per environment, same chart, same images, different site values. These files are **templates**
of the site values: copy them into the place your pipeline reads them from (the GitLab protected file variables
`VALUES_STAGING` and `VALUES_PRODUCTION`, or the private repository of your GitOps tool) and fill them there. They
hold no secret (secrets come from a Secret or an ExternalSecret) and no image digest (the pipeline sets the
digests of the images it just verified).

| Environment | Release / namespace | Deployed from | How | Data |
|---|---|---|---|---|
| development | Docker Compose on a laptop (`Z_ENV=development`) | any branch | `docker compose up` (deploy/README.md) | fakes only |
| staging | `z` in `z-staging` | every merge to `main` | `deploy:staging` (GitLab CI), automatic | test accounts and test projects, never production data |
| production | `z` in `z` | a signed tag `vX.Y.Z` | `deploy:production`, manual, approved in the protected environment | real |

Create and label each namespace once, before the first deploy (the release namespace must enforce Pod Security
`restricted`; the chart cannot own the namespace Helm stores its release in):

```bash
kubectl create namespace z-staging
kubectl label namespace z-staging pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest
```

The process around them (branches, versions, migrations, rollback, hotfix) is in docs/14-release-and-changes.md.
