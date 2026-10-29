# ADR 0019 — Take-over works on the checkpoint, not inside the runner

Status: accepted

## Context
docs/04 §5 lets a person pause a job, open its workspace "in a web terminal or editor (read-only by
default)", edit, and resume; docs/02 says pause and take-over use the checkpoint mechanism. A shell into the
runner would need `exec` into the container (the socket proxy refuses it on purpose) or a terminal server in
every runner, and a person typing in the agent's sandbox breaks the "agents hold no credentials, every write
is reviewed" story less visibly than files do.

## Decision
1. **No shell in v1.** Take-over is a web editor over the workspace files. `terminal_url` points to the
   take-over page of the web app.
2. **The workspace is rebuilt server-side** from what Z already holds: the staged inputs (same staging and
   judge as for the runner; cached per job while the session lasts) plus the files the agent added, changed or
   deleted, read from the job's last checkpoint (`workspace.tar.gz`). The checkpoint format moved to z-common.
3. **Sessions.** Starting a take-over pauses the job and opens a 30-minute session (renewed by each call) for
   one person; anyone else gets a conflict. read_only lists and reads; read_write also writes and deletes
   files (1 MiB per file, 8 MiB per take-over, paths kept inside the workspace, never `.z-*`). read_write needs
   a checkpoint (the job finished at least one step). Every start, edit and end is audited.
4. **Ending.** Without edits the same runner simply resumes. With edits, they are folded into the checkpoint's
   workspace delta, the agent gets a guide message naming the changed and deleted files, and the job is
   requeued from the checkpoint step without consuming an attempt. The parked runner's tokens are cleared at
   once; the scheduler does not claim a queued job whose old runner is still referenced and its reaper stops
   that runner first, so a fresh runner restores the edited workspace.

## Consequences
- Take-over works the same on Docker, k3s and Kubernetes and needs no runner change and no exec rights.
- People cannot run commands in the workspace; a web terminal (asciicast-recorded, in a separate sandbox fed
  with the same rebuilt workspace) is left for later.
