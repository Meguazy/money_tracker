# Upgrade and Rollback

- Status: implemented Step 7 operational procedure
- Applies to: application code, Python and frontend dependencies, database
  migrations, systemd user units, pinned containers, and promoted model/runtime
  configuration
- Decisions: [AD-005](../arc42/decisions/AD-005-application-postgresql.md),
  [AD-013](../arc42/decisions/AD-013-local-runtime-and-backup.md), and
  [AD-022](../arc42/decisions/AD-022-pull-request-workflow.md)

This runbook upgrades one reviewed Money Tracker release on the target
workstation and chooses a safe recovery path when acceptance fails. It does not
make database downgrade safe, replace the clean-machine restore drill, or
authorize overwriting active data. Only the owner decides to accept data loss,
replace production storage, or rotate a credential.

## Non-negotiable rules

- Deploy only a commit merged to `main` whose required GitHub checks passed.
- Freeze every application writer before a schema or stateful-service change.
- Create and verify a fresh authoritative backup after the writers stop and
  before changing code, schema, images, units, or active storage.
- Keep the previous reviewed commit, container digests, model identity, schema
  revision, and backup snapshot identifier until acceptance finishes.
- Never use Alembic downgrade, manual row edits, an offset reset, volume
  deletion, `docker compose down -v`, or Restic pruning as rollback.
- Never point production services at a partial or unverified restore.
- Do not put `.env`, credentials, owner content, database URLs, private paths,
  trace payloads, or receipt hashes into an upgrade record or pull request.

## Classify the change before downtime

| Change | Required evidence | Rollback class |
| --- | --- | --- |
| Documentation only | Documentation and Git policy gates | No deployed runtime change |
| Application code or unit only, schema unchanged | Official gates plus the affected real-service acceptance | Return to the previous reviewed commit |
| Additive database migration | PostgreSQL integration, backup drill, forward migration, and explicit proof that the previous release tolerates the new schema | Previous commit only when compatibility is documented; otherwise restore/forward-fix |
| Destructive or incompatible schema/state change | Migration-specific backup, restore, and cutover plan in the implementing PR | No in-place rollback |
| Pinned container digest | Both Compose profiles and the owning service's real integration/health checks | Previous digest only when its on-disk schema remains compatible |
| Prompt, model, threshold, tool, or routing policy | Applicable evaluation gates and decision-log promotion | Restore the previously promoted complete configuration |
| Secret rotation | Boundary-specific validation without printing the value | Rotate forward; never restore a compromised secret |

An implementing pull request must state its class. If compatibility is unknown,
treat the change as incompatible.

The Step 7 `20260923_0008` default-source reconciliation is an incompatible
revision for the previous release's exact schema checks even though it changes
only metadata. Its production deployment needs the isolated recovery proof of
the fresh pre-upgrade snapshot described in
[Backup and Restore](backup-and-restore.md#legacy-default-source-recovery-2026-09-23).
After migration, take and drill a new snapshot. Do not return to the previous
code in place; use a reviewed forward fix or an isolated restore and separately
authorized cutover if the candidate fails.

## Prepare the release record

Record only safe identifiers in a private operator note:

- UTC start time and planned maintenance window;
- previous and candidate Git commit IDs;
- current and candidate Alembic revisions;
- changed container tags and digests, if any;
- promoted model/configuration decision, if changed;
- pre-upgrade backup snapshot ID and age;
- queue counts, active pending-action count, and checkpoint disposition;
- commands/gates run, exact passed/skipped counts, and final outcome.

Do not copy journal payloads or trace contents into that record.

## Preflight while the current release is live

1. Confirm the deployed checkout is clean and on `main`, record its commit,
   then fetch the reviewed candidate without changing the files used by the
   running release:

       git switch main
       git status --short --branch
       git log -1 --format='%H %s' HEAD
       git fetch origin
       git log -1 --format='%H %s' origin/main

2. Review `HEAD..origin/main`, including every migration file. Confirm the pull
   request states the rollback class and every changed external boundary. Do
   not fast-forward this deployment checkout yet: the pre-upgrade backup must
   run with the current reviewed release's code and locked environment.
3. Check current schema, queue, services, backup freshness, and schedules using
   content-free interfaces:

       uv run --env-file .env money-tracker-db current
       uv run --env-file .env money-tracker-telegram health
       uv run --env-file .env money-tracker-backup status
       systemctl --user --no-pager --state=running --type=service \
         'money-tracker-*'
       systemctl --user list-timers --all 'money-tracker-*' --no-pager

4. Confirm the candidate pull request passed the applicable official gates in
   [Development](../development.md#repeatable-checks). Any model, Telegram,
   installed-systemd, Langfuse, browser, PostgreSQL, or backup boundary change
   also needs its documented real local evidence, collected from the candidate
   branch or a separate checkout without changing this live checkout.
5. Abort before downtime if the current database is unhealthy, the last
   verified snapshot is stale, the backup target is unavailable, a required
   artifact is missing, or compatibility is unresolved.

## Freeze application writers

Announce the maintenance window. Then prevent new external and browser writes:

    systemctl --user stop money-tracker-telegram.service
    systemctl --user stop money-tracker-api.service

With ingress stopped, inspect `money-tracker-telegram health`. Let eligible
work finish when dependencies are healthy. A paused owner review, a dead letter,
or a future retry is durable and need not be discarded. Then stop the worker:

    systemctl --user stop money-tracker-worker.service

Stop the three operational timers so none starts during migration or restore;
this does not interrupt an already running oneshot, so verify the corresponding
services are inactive before continuing:

    systemctl --user stop money-tracker-backup.timer
    systemctl --user stop money-tracker-trace-retention.timer
    systemctl --user stop money-tracker-runtime-retention.timer
    systemctl --user is-active money-tracker-backup.service \
      money-tracker-trace-retention.service \
      money-tracker-runtime-retention.service

The expected service result is `inactive`. Do not kill a backup or retention
transaction. The local model and Langfuse may remain running because neither
owns authoritative application state.

## Create the rollback point

With application writers frozen, create and verify a fresh authoritative
snapshot:

    uv run --env-file .env money-tracker-backup create
    uv run --env-file .env money-tracker-backup status

Record the safe snapshot ID, creation time, age, previous commit, and current
schema revision. Stop the upgrade if creation, repository verification,
retention, or status fails. The snapshot includes application PostgreSQL,
retained receipt originals, and recovery configuration; it deliberately does
not include Langfuse or model weights.

## Install the reviewed release

Run only the steps the reviewed change needs, in this order:

1. Re-read the already fetched `origin/main` identifier and compare it with
   the candidate recorded during preflight. Only when they match, fast-forward
   to that reviewed commit; refuse divergence or a different head:

       git log -1 --format='%H %s' origin/main
       git merge --ff-only origin/main
       git log -1 --format='%H %s'

2. Update the existing environment without rotating values, then reproduce the
   locked Python environment:

       ./scripts/upgrade_local_env.sh
       uv sync --locked --all-groups

3. When frontend sources or dependencies changed, reproduce the compiled
   assets:

       cd frontend
       npm ci
       npm run typecheck
       npm run test:ci
       npm run build
       cd ..

4. Validate both Compose profiles. Change a stateful container only through
   [Container Versions](container-versions.md#controlled-upgrade); never pull a
   mobile tag implicitly:

       docker compose --env-file .env.example config --quiet
       docker compose --env-file .env.example --profile observability config --quiet

5. Start or update application PostgreSQL as the implementing change
   documents, wait for health, and apply forward migrations before any process
   that requires them:

       docker compose up -d --wait application-postgres
       uv run --env-file .env money-tracker-db bootstrap
       uv run --env-file .env money-tracker-db current

6. Render the reviewed unit definitions and validate them:

       ./scripts/install_systemd_units.sh
       systemd-analyze --user verify \
         money-tracker-api.service \
         money-tracker-telegram.service \
         money-tracker-worker.service \
         money-tracker-llama.service \
         money-tracker-backup.service \
         money-tracker-trace-retention.service \
         money-tracker-runtime-retention.service

If any step fails, leave ingress stopped and follow the decision tree below.

## Start and accept the candidate

Start inference and the read/control surface before any workflow processor:

    systemctl --user start money-tracker-llama.service
    systemctl --user start money-tracker-api.service

Wait for actual readiness, not only `Type=simple` process state:

    curl --fail http://127.0.0.1:8000/api/v1/health/live
    curl --fail http://127.0.0.1:8000/api/v1/health/ready

After database, inference, and API acceptance, start the worker and inspect the
durable queues before reopening ingress:

    systemctl --user start money-tracker-worker.service
    systemctl --user is-active money-tracker-worker.service
    uv run --env-file .env money-tracker-telegram health

Run every acceptance named by the implementing change. At minimum, confirm the
schema revision, API readiness, worker stability, queue state, backup freshness,
and that no core readiness check depends on Langfuse. A tracing-boundary change
must additionally pass its real Langfuse-off gate. Then reopen ingress:

    systemctl --user start money-tracker-telegram.service

For each operational timer recorded as enabled before maintenance, start that
timer and no disabled schedule:

    systemctl --user start money-tracker-backup.timer
    systemctl --user start money-tracker-trace-retention.timer
    systemctl --user start money-tracker-runtime-retention.timer

`start` preserves the units' prior enablement state. Confirm enabled/active
state and the next timer occurrences. Complete a real Telegram smoke only when
the change touches that workflow; do not create a finance record merely as a
process-liveness probe.

Keep the rollback point until the observation window named by the change has
passed. Never prune it manually; normal Restic retention owns its lifetime.

## Rollback decision tree

### Failure before a migration or stateful change

Keep writers stopped. Return to the previous reviewed commit, reproduce its
locked environment and frontend, reinstall its units, and repeat its official
gates. Start services in the acceptance order above. No database restore is
needed because authoritative state did not change.

### Failure after a compatible additive migration

Return to the previous release only when its pull request explicitly proved
that it can operate on the new schema. Rebuild and reinstall the complete prior
release; do not run a downgrade. If compatibility was not proved, treat the
failure as incompatible.

### Failure after an incompatible migration or stateful upgrade

Keep every application writer stopped and preserve the failed database or
volume for diagnosis. Do not downgrade or replace it in place. With the
application version that created the pre-upgrade snapshot:

1. restore that snapshot into a new empty isolated database and receipt root;
2. run `money-tracker-backup verify-restore` and the applicable integration and
   health gates;
3. compare safe counts and checkpoint disposition with the release record;
4. choose a forward fix, or ask the owner to authorize a separately reviewed
   production cutover to the verified restore.

The implemented restore drill deliberately cannot overwrite production. A
production cutover is not automated in V1 because it can discard every write
after the rollback snapshot. Record the resulting RPO and retain the failed
storage until the replacement is accepted.

### Container failure

Return to previous image digests only if upstream and local evidence say the
current on-disk schema is backward-compatible. Container rollback is not data
rollback. Otherwise preserve the volumes and use the isolated restore path in
[Container Versions](container-versions.md#rollback).

### Model or model-visible configuration failure

Restore the complete previously promoted model, prompt, schema, tool,
threshold, and policy set recorded by its evaluation decision. A read-selection
policy may use its documented direct rollback only when the rest of that
promoted configuration is unchanged. Model weights are outside the
authoritative backup, so verify the reacquired artifact digest before service
start.

### Credential problem

Never roll back to an exposed credential. Keep the affected boundary stopped,
rotate forward through its documented hidden-input procedure, validate without
printing the value, and inspect the journal for content-free failure events.

## Failed upgrade and handoff

Before ending a failed maintenance window:

- keep unsafe writers disabled and leave unrelated durable ingestion running
  only when it cannot widen the incident;
- record safe reason codes, timestamps, commit/revision/digest identifiers,
  snapshot ID, and service states;
- confirm which database, volumes, archive, and configuration copy are
  authoritative;
- open a corrective pull request for code or documentation changes;
- update this runbook, affected ADRs, arc42 views, and the roadmap when the
  failure reveals a missing contract.

Never improvise a destructive recovery to shorten downtime. A stopped service
with preserved data is safer than a fast, unverified rollback.
