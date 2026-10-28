---
name: commit-and-mr
description: Prepare the merge request proposal.
version: 1.0.0
---

Title: `<type>(<scope>): <summary>` (conventional commits).
Description: Why, What changed, How tested, Risks. Link the issue or finding.
Propose `gitlab.merge_requests.create` with payload `{project, source_branch: z/<mission-id>/<short-name>, target_branch, title, description, patch_artifact}`. The executor applies the patch, commits as the Z bot, pushes with its write credential and opens the MR after approval. Never push yourself.
