# Packs

A pack bundles agents, skills, playbooks, connectors, schemas, objectives and evals for one domain. These are **seeds**: complete and correct enough to load, meant to be finished during BUILD workstream B7.

```
<pack>/
├── pack.yaml          name, version, description, owner, requires_connectors
├── agents/            one YAML per agent (rename display_name/handle/avatar freely; keep `name`)
├── skills/<name>/     SKILL.md (AgentSkills format) + optional scripts/ and references/
├── playbooks/         reusable missions: team, steps, fan-out, writes, trigger, budget
├── connectors/        connector templates (credentials are references, never values)
├── schemas/           JSON Schemas for structured outputs
├── objectives/        example standing objectives for the pack's agents
└── evals/             JSONL eval cases per agent or skill
```

| Pack | Agents |
|---|---|
| `core` | Orchestrator, Browser Agent, Agent Builder |
| `vuln` | Vuln Analyst |
| `ticketing` | Ticket Manager |
| `dev` | Dev Engineer, Reviewer |
| `archi-isp` | Security Architect |
| `workplace` | Assistant, Writer, Analyst, Researcher |

Rules for skills: short numbered steps, explicit output format with an example, no implied knowledge (they run on open-weight models). Tools are least privilege; writes are always `propose_action`.
