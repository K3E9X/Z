# Injection corpus

Fixtures for the judge injection scan (docs/04 §6, BUILD B6 "judge tests on an injection corpus").

## `corpus.jsonl`
One JSON object per line:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Stable id, `attack-NNN` or `benign-NNN` |
| `text` | string | The content as a tool or workspace would return it |
| `label` | `attack` \| `benign` | Expected judge verdict |
| `category` | string | Attack family (e.g. `instruction_override`, `role_play`, `exfiltration_tool`, `exfiltration_url`, `credential_request`, `tool_abuse`, `approval_abuse`, `encoded`, `hidden_markup`, `multilingual`) or benign family (`vuln_description`, `accepted_risk_ticket`, `ticket`, `readme`, `security_advisory`, `policy`, `code_comment`, `multilingual`) |

Attack entries may add `verdict` (`flagged` | `blocked`: what the deterministic stage must return; default
`flagged` or stronger) and `signals` (categories the deterministic stage must report, see `server/src/z_server/judge.py`).

Benign entries look security-related on purpose (vulnerability text, "ignore this finding, accepted risk" tickets,
README instructions) so the judge is measured on false positives as well as detections.

## Status
The file holds 44 benign entries and the first 17 attack entries (`server/tests/test_judge.py` runs them all
against the deterministic stage). The rest of the **attack** half (target: 40+ entries across the families
above, including more encoded and French examples) and the carrier files (ticket JSON, README.md, HTML
page embedding attacks) still have to be added by the judge workstream (B6). Keep the same format and id scheme.

The fake tools seed already carries three embedded injection strings that e2e tests can use
(`tests/fakes/fake_tools/seed.py`: `INJECTION_TICKET` in Jira SECOPS-4795, `INJECTION_README` in GitLab
`infra/ops-scripts` README.md, `INJECTION_PAGE` in Confluence page 10005).
