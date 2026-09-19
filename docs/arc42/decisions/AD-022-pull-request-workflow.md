# AD-022: Pull-Request Workflow with Merge-Blocking CI

- Status: accepted
- Implementation: implemented; shared policy checker, versioned Git hooks,
  Claude Code guardrails, pull-request template, development process, hosted
  GitHub Actions (`ci` with its aggregate `ci-gate`, `pr-metadata`, and the
  non-required Langfuse workflow), Dependabot, and the `protect-main` ruleset,
  active since 2026-09-19 and proven to block a failing merge
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

The second slice adds least-privilege GitHub Actions for hosted gates and one
aggregate required `ci-gate`, plus an independently required metadata workflow.
Jobs use generated disposable credentials and never owner secrets. Model/GPU,
real Telegram, installed-service, and model-backed Langfuse gates remain local
and become mandatory when a slice touches their boundary.

The third slice versions and, after explicit owner confirmation, applies a
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
  This account has Pro through GitHub Student; if that lapses, GitHub stops
  enforcing the ruleset.
- With no bypass actors, a GitHub Actions outage or a required job broken by
  its environment blocks every merge until it is fixed.

## Implementation evidence

The first slice adds `CLAUDE.md`, the PR template, `git_policy` and its unit
tests, executable hooks and their idempotent installer, the tested Claude Bash
guard and shared settings, and the workflow/runbook material in
`docs/development.md`. Pipeline runs, measured job minutes, the ruleset, and
the negative merge test are intentionally deferred to their dedicated slices.

The second slice adds `.github/workflows/ci.yml` with nine least-privilege jobs
(`python-static`, `python-tests`, `postgres-integration`, `backup-drill`,
`frontend`, `e2e-browser`, `compose-and-deploy`, `shell-and-workflows`, and
`security`) aggregated by `ci-gate`, which fails unless every job succeeds.
`pr-metadata.yml` runs the same `git_policy` module over the PR title, branch,
commit range, and template sections. `langfuse.yml` runs the model-free
Langfuse gates weekly and on demand; it can run only from the default branch.
Actions are pinned by full commit SHA; downloaded tools are pinned by version
and SHA-256. Service jobs generate disposable credentials, reject a JUnit report
with no executed test or a skip caused by their own missing flag through
`money_tracker.development.ci_reporting`, and upload failure evidence only after
`money_tracker.development.ci_redaction` removes generated credentials.
`.gitleaks.toml` allowlists two triaged historical placeholders, and
`.github/dependabot.yml` groups weekly updates with a seven-day cooldown. The
pull request records the first runs, each job's billable minutes, and the
monthly projection; [Development](../../development.md#local-and-ci-gate-correspondence)
maps every job to its local gate.

The third slice versions the ruleset in `.github/rulesets/main.json` and, after
the owner confirmed it, applied it on 2026-09-19 as `protect-main` (id
23686462): default branch, active, no bypass actors, pull requests with zero
approvals and required conversation resolution, strict `ci-gate` and
`pr-metadata` checks from GitHub Actions, and blocked force pushes and
deletion. The file equals the live ruleset, including the
`require_extra_approval_for_unattributed_changes` default, which has no effect
with zero approvals. A throwaway pull request (#7) with a conforming commit
that broke the frontend type check and a non-conforming title turned
`frontend`, `e2e-browser`, `ci-gate`, and `pr-metadata` red, and GitHub
reported `mergeStateStatus: BLOCKED`; it was closed unmerged and its branch
deleted. The same slice narrows the Claude Code deny patterns and rewrites the
Bash guard to parse commands, so branch names containing `main` and messages
containing `-n` pass, while `-n` clusters, pushes to `main` in every refspec
form, and agent writes to rulesets, branch protection, or repository settings
are refused. The development-policy tests now isolate themselves from the Git
environment a hook exports, after a hook run in a linked worktree had written
into the real repository during the negative proof.
