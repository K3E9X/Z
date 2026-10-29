# Architecture decision records

Format: context, decision, consequences. Add a new file for every decision the spec did not already make (`NNNN-short-title.md`).

- [0001 — OpenHands Software Agent SDK as the agent harness](0001-openhands-sdk-runtime.md)
- [0002 — LiteLLM is the only path to any model](0002-litellm-single-egress.md)
- [0003 — A tool gateway owns credentials and policy](0003-tool-gateway.md)
- [0004 — Postgres queue and checkpoints in v1](0004-postgres-queue.md)
- [0005 — Writes are proposals applied by a separate executor](0005-propose-action.md)
- [0006 — Skills use the AgentSkills `SKILL.md` format](0006-agentskills-format.md)
- [0007 — One Helm chart for k3s and Kubernetes, one Compose file for Docker](0007-one-chart-three-targets.md)
- [0008 — Textual for the interactive CLI and `z top`](0008-textual-cli.md)
- [0009 — The learning loop proposes; humans apply](0009-learning-needs-approval.md)
- [0010 — GitLab as the identity provider (OIDC), roles from GitLab groups](0010-gitlab-oidc-identity.md)
- [0011 — Details fixed while writing contracts v1](0011-contract-v1-details.md)
- [0012 — Details fixed while building missions, scheduler and review](0012-missions-slice-details.md)
- [0013 — Judge stages: pattern floor, raise-only model, pre-action checks](0013-judge-stages.md)
- [0014 — Learning loop and evals: how they run for real](0014-learning-and-evals.md)
- [0015 — Personal OAuth connections and chat/mail channels](0015-connections-and-channels.md)
- [0016 — Browser connectors: reviewed flows replayed by z-browser](0016-browser-flows.md)
