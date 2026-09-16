# Security and Troubleshooting

- Status: implemented Steps 1–4 controls plus the current Step 5 web controls
- Scope: one-owner local workstation, accepted Telegram boundary, and loopback web boundary

## Trust boundaries

Trusted only after validation and authorization:

- application/domain code and pinned configuration;
- the paired owner profile resolved from local state;
- committed PostgreSQL records that passed domain invariants;
- versioned prompts, schemas, tools, and taxonomy from the deployed commit.

Always untrusted data:

- Telegram users, messages, callbacks, captions, files, filenames, and metadata;
- receipt pixels and OCR/VLM text, including text that resembles instructions;
- model output and model-selected tool arguments;
- browser requests, even from a page that claims to be localhost;
- environment input, imported/exported files, backup paths, logs, and external
  service errors.

The local model is not an authorization authority and does not receive a generic
database or shell tool.

## Secret inventory and handling

Secrets include:

- production/test Telegram bot tokens;
- application and Langfuse database/service credentials;
- Langfuse project secret key;
- Restic password/key;
- random per-browser CSRF values while valid;
- token-bearing Telegram file URLs and any one-use pairing code while valid.

Controls:

- store secrets in mode-restricted, Git-ignored files or equivalent protected
  local secret sources;
- pass stable identity/profile context from trusted runtime code, not model
  arguments;
- redact secrets before logs, traces, exception rendering, API responses, and
  diagnostic bundles;
- avoid secrets in command-line arguments because process listings and shell
  history may retain them;
- rotate any secret that enters Git, an AI chat, a screenshot, a trace, or an
  untrusted artifact.

The current `.env` generator refuses overwrite and creates a mode-600 file. New
secret-generation behavior must preserve those properties.

## Telegram authorization

The implemented gateway accepts only the paired private user/chat. It validates the
source before downloading media, creating an inbox work item, invoking a model,
or reading finance data. Group support is disabled. Pairing codes expire, are
single-use, and cannot replace an existing owner without a local administrative
action.

Because Telegram is external and bot chats are not end-to-end encrypted, do not
ask the owner to send bank credentials, government identifiers, backup keys, or
other secrets through the bot.

## Prompt injection and model controls

- System/developer contracts, schemas, and tool registries come from deployed
  code and are separated from user/receipt data.
- Receipt text such as `ignore previous instructions` remains a product
  description string; it cannot grant tools or change graph routing policy.
- Profile IDs, idempotency keys, timezone, repository handles, and authorization
  are runtime-injected and absent from model-writable schemas.
- Query/advice agents receive only read tools. Financial mutation occurs in
  deterministic nodes after schema, invariant, and authorization checks.
- Structured output gets one repair attempt. Continued invalid output becomes a
  typed failure/clarification, never free-form execution.
- Traces omit image bytes and token-bearing URLs.

## Local web controls

Loopback binding alone does not remove browser threats. The implemented Step 5
surface enforces these controls:

- typed configuration refuses every non-IPv4-loopback listener;
- exact Host and Origin allowlists reject DNS rebinding and cross-origin reads;
- CORS remains closed; no permission header is emitted;
- unsafe methods require an allowed Origin and matching random HttpOnly-cookie
  and header values;
- movement mutations require a separate `Idempotency-Key` and observed
  optimistic version where applicable;
- strict bodies, active owner-scoped references, and domain lifecycle rules are
  validated before authoritative replacement;
- API responses are `no-store`; the compiled UI and API share one origin and
  receive restrictive CSP,
  referrer, MIME-sniffing, and framing headers;
- API responses return no environment, profile identity, private path, or secret.

The advice route is a read-only `POST`: CSRF protects its content-bearing body,
but it accepts no idempotency key or profile identity and its composed object
graph contains no mutation adapter. It returns only a bounded discriminated
union built from deterministic promoted-tool output; requests, prompts,
completions, profile data, credentials, URLs, paths, receipt media, and
infrastructure details are excluded. Its journal records only stable outcome,
tool, and reason codes, never owner text or financial content.

The profile-settings response is an explicit allowlist: fixed `it-IT`, `EUR`,
and `Europe/Rome` facts plus minimal active source choices. It contains no
generic configuration map and has no mutation route for those fixed facts. The
only editable choice uses the existing default-source command with CSRF,
idempotency, optimistic versions, and atomic audit.

The implemented receipt routes read and transition metadata only. They accept
a receipt ID, an observed version, and the transition-wide command key, and
they return no content digest, archive path, or image. Future routes that do
touch media must still validate IDs, versions, file paths, content types, and
decoded image limits. LAN or remote access requires a new authentication/TLS
threat model and ADR.

## Media controls

- Implemented: the local adapter stores originals outside presentation roots
  under sharded SHA-256 names, never Telegram filenames.
- Implemented: Pillow fully decodes only single-frame JPEG/PNG/WebP and enforces
  20 MiB, 40-megapixel, and 10,000-pixel-side limits.
- Implemented: directories/files use `0700`/`0600`, writes are atomic and
  no-overwrite, and reads refuse symlinks and verify the digest.
- Generate derivatives without trusting metadata paths; decide explicitly
  whether EXIF is retained in the private original and strip it from derivatives.
- Workflow/checkpoint state stores media IDs/references only.
- Product deletion is audited and consistent with database references and
  backup retention; manual filesystem deletion is unsupported.

## Logging and observability

Use structured event identifiers, pseudonymous profile/session/workflow IDs,
component versions, outcome/status, latency, retry number, and safe error codes.
Do not log raw environment dictionaries, database DSNs with passwords, full
Telegram updates by default, token-bearing URLs, receipt bytes, or unredacted
personal finance prompts in general system logs.

Langfuse is local and opt-in, but local does not mean unrestricted. Trace only
the content necessary for the selected development/evaluation purpose and apply
the private dataset policy.

### Reading the journal

AD-019 gives every process one JSON object per line on standard error. Two
settings control it, and both are written by the environment generator:

    LOG_LEVEL=INFO        # DEBUG adds per-step records
    LOG_FORMAT=json       # text renders the same fields for a terminal

Standard out is not part of this: several commands print machine-readable JSON
there as their result, so a pipeline keeps working while the journal goes to
standard error.

Every record carries `timestamp`, `level`, `logger`, and `event`. The event is
a stable dotted name that never contains a value, so it can be counted:

    journalctl --user -u money-tracker-worker -o cat \
      | jq -r 'select(.level=="ERROR") | .event' | sort | uniq -c

Correlation identifiers ride along with the operation. To follow one Telegram
update through ingestion, dispatch, persistence, and delivery:

    journalctl --user -u money-tracker-worker -o cat \
      | jq 'select(.inbox_id=="<uuid>")'

Levels are not decoration. `WARNING` is something the system already handled,
such as `work_unit.retry_scheduled` or `gateway.outbox.retry_scheduled`.
`ERROR` needs an owner: `work_unit.dead_lettered`, `transport.request.rejected`,
`expense.idempotency_conflict`. `CRITICAL` means the process is ending or
authoritative data is in doubt, such as `process.unhandled_exception` or
`backup.restore.dump_checksum_mismatch`. Start a triage at `CRITICAL` and
`ERROR`; a burst of `WARNING` with no `ERROR` usually means a dependency is
flapping rather than broken.

Useful starting queries:

| Question | Event to look for |
| --- | --- |
| Did anything crash? | `process.unhandled_exception`, `thread.unhandled_exception` |
| Why was a message not understood? | `routing.clarification`, with its `reason_code` |
| Why did a work unit fail? | `work_unit.dead_lettered`, `work_unit.retry_scheduled` |
| Is Telegram refusing us? | `transport.request.rejected`, `transport.request.transient_failure` |
| Is the model endpoint up? | `inference.endpoint.unavailable`, `model_boundary.exhausted` |
| Did a browser request get blocked? | `web.request.blocked`, with its `reason_code` |
| Is backup still running? | `backup.create.completed`, `backup.snapshot.absent` |

Raise detail only while diagnosing, then put it back:

    LOG_LEVEL=DEBUG uv run --env-file .env money-tracker-worker process-once

The journal is diagnostic, not authoritative. It is not backed up, and it is
not the audit trail: PostgreSQL owns both. Credential redaction covers
messages, fields, and rendered tracebacks, but it recognizes token shapes only,
so keeping owner content out of records remains a code-review rule rather than
something the filter can enforce.

## Troubleshooting order

Before changing state:

1. Identify whether the failing capability is current or merely planned.
2. Check repository status, active roadmap step, pinned versions, and recent
   migration/decision changes.
3. Inspect service/process health without printing environment secrets.
4. Determine the authoritative owner of the failing data: finance PostgreSQL,
   messaging, workflow checkpoint, receipt store, or Langfuse.
5. Capture redacted error codes/timestamps and the smallest necessary logs.
6. Check whether a current verified backup exists before migration repair,
   replay, pruning, or deletion.
7. Prefer replay/restart/retry paths designed for the component; do not edit
   authoritative rows manually without a documented repair procedure.

## Failure triage

| Symptom | Safe first checks | Do not do |
| --- | --- | --- |
| Langfuse web becomes unhealthy again | Inspect web/worker/ClickHouse logs and confirm the pinned non-cluster flag remains effective. | Delete volumes or force migration state. |
| CLI cannot reach model | Verify configured loopback endpoint, llama-server process, model path, build/GPU compatibility, and health response. | Print the full `.env` or replace locked dependencies ad hoc. |
| Telegram gateway receives nothing | Verify token via redacted `getMe`, no webhook, gateway/network health, persisted offset, and paired owner. | Regenerate/delete the bot before checking delivery mode and logs. |
| Same Telegram update appears again | Check inbox unique key and whether offset advanced only after commit. | Delete duplicate rows or re-run mutation without its original idempotency key. |
| Worker retries indefinitely | Verify five-attempt transition, dependency state, typed error class, and dead-letter record. | Skip the record by mutating offsets or mark it successful manually. |
| Expense appears twice (critical incident) | Stop the workflow worker, preserve database/log evidence, compare source update/command keys and audit entries. | Delete one row before identifying invariant/idempotency failure. |
| Budget/chart total differs (planned) | Compare posted category allocations, occurrence timezone, refund/void status, and parent roll-up. | Add parent expense totals to receipt-line totals. |
| Receipt will not reconcile (planned) | Inspect visible line totals, discount/rounding, payment split, and unexplained difference. | Force posted status or invent a balancing line silently. |
| Unauthorized bot access (critical incident) | Stop gateway, rotate token if exposed, preserve redacted source IDs/audit, verify owner mapping. | Reply with finance data or expose which user is authorized. |
| Backup failed | Check destination mount, free space, repository access, absolute-path and owner-only input settings, and `money-tracker-backup status` for the last verified snapshot age. | Prune old snapshots or delete staging before preserving diagnostics. |
| Restore mismatch | Keep the restored target isolated; compare manifest, dump/media hashes, application/migration versions, and integrity checks. | Point production services at the partial restore. |

## Critical incident priorities

For duplicate financial writes, unauthorized access, secret exposure, corrupted
authoritative data, or failed restore:

1. stop the narrow component causing new risk while preserving unrelated durable
   ingestion where safe;
2. preserve recoverable evidence and take a consistent backup if data integrity
   allows;
3. rotate exposed credentials;
4. identify affected stable IDs and time range through audit/idempotency records;
5. repair through a reviewed, tested migration or domain operation;
6. add deterministic tests/evaluation regressions and update risk/ADR/runbook;
7. verify restore and normal operation before resuming automation.

Do not put private incident contents into Git. Commit only anonymized root cause,
control changes, and safe regression fixtures.
