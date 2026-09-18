# AD-022: Pull-Request Workflow with Merge-Blocking CI

- Status: accepted
- Implementation: partially implemented; shared policy checker, versioned Git
  hooks, Claude Code guardrails, pull-request template, and development process
  delivered; GitHub Actions and server-side protection remain planned
- Date: 2026-09-19

## Context

The sole owner historically committed directly to `main`. The repository had
no versioned hooks, agent permissions, pull-request template, required checks,
or protected branch. That made correctness depend on each tool invocation and
left no server-side boundary able to reject an invalid merge.

Money Tracker already has diverse gates: fast Python checks, real PostgreSQL
integration, backup/restore, frontend and browser tests, deployment validation,
security audits, and owner-controlled model, Telegram, Langfuse, and installed
service evidence. A useful workflow must keep one policy source, preserve the
real-service test contract, and distinguish gates a hosted runner can execute
from gates that require the owner's workstation.

## Decision

Each vertical slice uses one typed kebab-case branch and one pull request. Only
the owner merges. Branches stay linear by rebasing on `main`, and roadmap-step
closure is a separate evidence PR after owner acceptance.

`money_tracker.development.git_policy` is the single executable policy for
branch names, Conventional Commit headers, sole authorship, forbidden
co-author trailers, autosquash commits, and merge commits in a PR range. Local
Git hooks and pull-request metadata CI invoke that module rather than duplicate
its rules.

Versioned hooks reject commits on `main`, invalid messages, remote `main`
updates, and failed fast checks. Claude Code adds declarative denies plus a
tested `PreToolUse` Bash guard. The guard exits 2 for an intentional block and
fails open for its own errors, matching Claude Code's documented hook contract.
Commit and pull-request attribution are disabled.

The next slice adds least-privilege GitHub Actions for hosted gates and one
aggregate required `ci-gate`, plus an independently required metadata workflow.
Jobs use generated disposable credentials and never owner secrets. Model/GPU,
real Telegram, installed-service, and model-backed Langfuse gates remain local
and become mandatory when a slice touches their boundary.

The final slice versions and, after explicit owner confirmation, applies a
`main` ruleset with no bypass actors, zero required approvals, strict required
checks, conversation resolution, and blocked force-push/deletion. Local
guardrails are preventive only; server-side protection is the guarantee.

## Alternatives considered

- **Continue direct commits to main.** Rejected because neither local mistakes
  nor failing checks can be stopped before authoritative history changes.
- **Use only local hooks.** Rejected because hooks are locally configurable and
  can be bypassed.
- **Use only hosted CI without branch protection.** Rejected because red checks
  would remain advisory.
- **Require approvals from another developer.** Rejected because the repository
  has one owner/developer; required approvals are zero while checks remain
  mandatory for administrators.
- **Require linear history server-side.** Rejected because the owner retains
  merge commits. Slice branches themselves remain linear.

## Consequences

- Every change gains a reviewable PR record and exact validation evidence.
- Agents stop after opening a PR and cannot merge or begin dependent work.
- Local pushes run the fast service-independent gate, while slower hosted jobs
  consume a finite monthly Actions allowance that must be measured.
- Some acceptance remains owner-workstation evidence because mocking the model,
  Telegram, Langfuse, or installed services would invalidate the gate.
- The workflow cannot become merge-blocking until the Actions and protection
  slices land, and private-repository protection depends on GitHub Pro or Team.

## Implementation evidence

The first slice adds `CLAUDE.md`, the PR template, `git_policy` and its unit
tests, executable hooks and their idempotent installer, the tested Claude Bash
guard and shared settings, and the workflow/runbook material in
`docs/development.md`. Pipeline runs, measured job minutes, the ruleset, and
the negative merge test are intentionally deferred to their dedicated slices.
