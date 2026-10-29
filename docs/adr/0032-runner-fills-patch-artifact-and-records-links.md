# ADR 0032 — The runner fills `patch_artifact`; workspace links travel in the delta metadata

Status: accepted

## Context
Two runner gaps found by review:
- Playbooks whose output schema has `patch_artifact` (dependency upgrade, commit and MR) asked the agent to put the
  id of the patch artifact in its output. The agent cannot know that id before the runner uploads the patch, so it
  guessed, and a wrong id either failed validation or pointed `gitlab.merge_requests.create` at the wrong file.
- Symlinks the agent created (a virtualenv's `bin/python -> /usr/bin/python3`) were archive members of the
  workspace delta. The safe extraction refuses links that point outside the workspace, so a resume from such a
  checkpoint failed, and `apply_delta` followed links when it deleted paths.

## Decision
1. When the output schema has a `patch_artifact` property, the runner builds and uploads the patch before it
   validates the output, writes the artifact id into the output and `result.json`, and drops any value the
   agent set. With no change in the workspace the field stays missing and the repair message says why. The
   completion upload reuses the same artifact. Skills `dependency-upgrade` 1.0.2 and `commit-and-mr` 1.0.1 tell
   the agent not to set it.
2. The workspace delta records symlinks in its metadata (`links`: path → target), not as archive members. They
   are recreated after the files are extracted, only under a parent that resolves inside the workspace, never in
   `.z/`, never over a directory. Deletions unlink the path itself and never follow a link. Take-over edits
   (`z_common.checkpoint.merge_edits`) keep the links, except a path an edit replaced or deleted.

## Consequences
- The MR always carries the patch the runner produced, whatever the agent wrote.
- A link to a target outside the workspace survives a resume as a link; reading through it stays refused by the
  workspace jail.
