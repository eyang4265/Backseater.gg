<!-- 
# Codex + Claude Code collaboration

## Safety boundary

- Work only in the assigned Git worktree and branch. Never edit the main
  checkout or another agent's worktree.
- `main` is integration-only. Neither agent merges, force-pushes, deploys, or
  changes production configuration without an explicit task.
- Never read, copy, print, or put secrets in mail: `json/secrets.json`, `.env`
  files, tokens, and runtime account data are out of scope.
- Use separate local server ports and separate non-production databases when
  work requires them.

## Ownership

Every task must state an owner and a file scope. Only one agent may own shared
configuration, dependency lockfiles, database migrations, release files, or a
module touched by both tasks. If scope overlaps, send mail and wait for the
coordinator to divide or sequence the work.

Suggested split:

| Agent | Good work |
| --- | --- |
| Codex | feature implementation, module-level refactors, focused tests |
| Claude Code | independent bug fixes, test additions, review of a completed branch |

For a single feature, implementation and review are sequential: the reviewer
starts after the implementation branch has a committed handoff.

## Local mailbox

The mailbox is shared by all worktrees but is stored under Git metadata, so it
is not committed or merged. Check it when a session starts, before a risky
change, when blocked, and before finishing.

```sh
# View messages addressed to an agent.
scripts/agent-mail inbox codex
scripts/agent-mail inbox claude

# Send a short, non-secret handoff or question.
scripts/agent-mail send codex claude "question about tracker scope" "Do you own bot_app/tracker.py?"
scripts/agent-mail send claude codex "review ready" "Committed tests in abc1234; please review the edge case noted in the commit."
```

Mail is a coordination channel, not a source of truth. A completed task must
also include a commit hash, tests run, files changed, and any follow-up work.

## Task lifecycle

1. The coordinator assigns the agent, branch, file scope, acceptance checks,
   and a task name.
2. The agent reads its inbox, makes only scoped changes, runs focused tests,
   and commits an atomic change.
3. The agent sends a handoff with the commit hash, checks run, and risks.
4. The coordinator integrates one completed commit at a time, runs the test
   suite, then asks any remaining agent to rebase or adapt if necessary.

For this project the full local check is:

```sh
python3 -m unittest discover -s tests -t .
``` -->
