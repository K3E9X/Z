# ADR 0025 — cli connectors run reviewed argv templates in one-shot tool containers

Status: accepted

## Context
docs/05 §1: `cli` connectors expose each allowed subcommand of a command-line tool as an operation with a typed
argument schema, run "in a gateway-side sandbox". The spec does not say what the sandbox is, how arguments reach
the command, or how the tool gets its credential.

## Decision
1. **Manifest.** `cli: {image, env, timeout_s, output}` and one declared operation per allowed subcommand with
   `command`, an argv template. `image` is pinned by digest and must be listed in `executor.tool_images` (checked
   when the connector is saved and at each call). `command` replaces the image CMD (it follows the ENTRYPOINT
   when the image has one). Operation classes come from the platform classifier like every connector
   (`pods.delete` is destructive), so writes go through propose → approval → executor.
2. **No shell, no flag injection.** `{{args.<name>}}` placeholders are substituted inside argv elements; values
   are scalars, cannot start with `-`, cannot hold NUL or line breaks, at most 4096 characters. An element that
   is only an optional placeholder is dropped when the argument is absent. Every placeholder must be declared in
   the operation's input schema (pack lint and connector API).
3. **Credentials only in the environment.** `cli.env` takes literals and `{{auth.secret}}`,
   `{{auth.username}}`, `{{auth.password}}` from the read or write credential; credentials in `command` and
   arguments in `env` are refused. Kubernetes passes the whole environment through a per-run Secret, never the
   pod spec.
4. **The sandbox is the executor's.** Docker: a `z-tool-<id>` container through z-socket-proxy, whose new tool
   profile accepts only `Z_TOOL_IMAGES`, user 65534, a bounded argv, a tmpfs on /tmp, and the runners' host
   hardening (read-only root, no capability, no-new-privileges, runners network, CPU/memory/pids limits); the
   runner profile is unchanged. Kubernetes: a Job in the runners namespace (Pod Security `restricted`, the
   runners' NetworkPolicies, deadline = timeout + 10 s, deleted after the run with its Secret). The `process`
   executor (dev) has no sandbox: cli connectors are refused there. Outbound traffic only goes through the egress
   proxy (its allowlist must include the tool's API hosts).
5. **Results.** stdout is the result (parsed when `output: json`, at most 1 MB, `truncated` beyond), a non-zero
   exit is an error with the end of stderr, the timeout stops the run. The result goes through the gateway's
   redaction and judge scan like any tool result. On Kubernetes the pod log mixes stdout and stderr.

## Consequences
- Read-only kubectl, cloud CLIs or internal tools can be connected without giving agents a shell or a credential.
- Each call costs a container start (about a second on Docker, more on Kubernetes): cli connectors suit
  occasional reads and approved writes, not high-rate polling.
