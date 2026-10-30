# ADR 0022 — Z as an MCP server: four tools, each one a REST operation called as the user

Status: accepted

## Context
docs/03 §5 lists `/mcp` (Z as an MCP server: `start_mission`, `get_mission`, `list_playbooks`, `list_review`) and
docs/01 `z mcp serve`. The spec does not say how an MCP client authenticates, how permissions and audit apply,
or whether approvals can be given through MCP.

## Decision
1. **One tool table** in `z_common.mcp_tools`, used by the server endpoint and the CLI. Each tool maps to one
   REST contract operation (`POST /missions`, `GET /missions/{id}` + jobs, `GET /playbooks`, `GET /review`).
   Arguments are checked first (known names only, ids match `[A-Za-z0-9_-]{1,64}`), so no argument can reach
   another route.
2. **`/mcp` on z-server** (streamable HTTP, stateless, JSON responses), proxied by z-ui's nginx like `/api/`.
   Authentication is `Authorization: Bearer` with a personal API token (`zt_…`) or a CLI session token; cookies
   are ignored, so a page in a signed-in browser cannot use it. A tool call runs the REST operation in-process
   with the same bearer token and the client's address: deny-by-default permissions, token scopes, step-up,
   validation, rate limits and audit (actor = the user, user agent `z-mcp (<client>)`) are those of the API.
3. **`z mcp serve`** is the same table over stdio for local MCP clients, calling the REST API with the signed-in
   profile's token. Nothing but the protocol is written to stdout.
4. **No approval through MCP.** There is no tool to approve, reject, answer or change autonomy: a client can
   start work and read its state, approvals stay in Z Review with four-eyes rules (invariant 4).
5. Tool results are compact JSON with a link to the mission in Z. Their content (job outputs) comes from agents
   and tools: MCP clients must treat it as data, like every tool result.

## Consequences
- An organisation can drive Z from IDE assistants or other agent platforms with a scoped, revocable token.
- Adding a tool means adding one row to the table and its REST operation; no new authorisation code.
