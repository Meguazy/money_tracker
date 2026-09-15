# AD-019: Structured Application Logging

- Status: accepted
- Implementation: `money_tracker.structured_logging`, the redaction primitives
  in `money_tracker.logging_safety`, and instrumentation across every entry
  point, adapter, application service, and persistence boundary implemented
- Date: 2026-09-12
- Extends: [AD-013](AD-013-local-runtime-and-backup.md) and
  [AD-018](AD-018-telegram-processing-transactions.md)

## Context

AD-018 leaves health and recovery to operator commands and process exit codes,
so that a supervisor can distinguish a configuration failure from a dependency
failure without reading logs. That contract covers how a process stops. It does
not cover the much more common question of why a particular turn behaved the
way it did while the process kept running.

Before this decision, logging existed only where a specific incident had forced
it. `configure_safe_logging` set up a root handler that redacted Bot API
credentials, three of the nine entry points called it, and fourteen call sites
in the gateway, the transport, and the worker emitted interpolated sentences
such as `Worker committed work unit outcome=%s response=%s`. Six entry points,
every application service, every repository, the model boundary, the web
adapter, the receipt archive, and the backup service emitted nothing at all.

Three consequences followed. A line like that one is readable once and
unusable afterwards: nothing can count how many work units were dead-lettered
in a week, and nothing can follow one Telegram update through ingestion,
dispatch, persistence, and delivery, because no identifier is carried across
those components. Whole classes of failure were invisible — a rejected receipt
image, an idempotency conflict, a routing veto, a blocked browser request, a
backup that stopped running. And an exception nobody caught bypassed logging
entirely: the interpreter printed it to standard error, past the formatter and
past the credential filter that AD-010's privacy contract depends on.

The constraint that shapes the solution is the privacy contract. Owner message
text, receipt pixels, model completions, and monetary amounts are exactly the
data this system exists to keep local. A journal that absorbed them would
become a second, unmanaged copy of the ledger in a place backup does not cover
and file permissions do not protect.

## Decision

Emit one JSON object per line to standard error, built from a stable event name
plus named fields, through a single module every process shares.

- `money_tracker.structured_logging` owns process configuration, the formatters,
  the module logger, and the correlation context. `configure_logging()` is
  idempotent, reads `LOG_LEVEL` and `LOG_FORMAT`, installs the credential
  filter on every handler including ones it did not create, and routes uncaught
  exceptions on any thread through logging rather than the interpreter's
  printer. `configure_safe_logging` is replaced by it.
- `money_tracker.logging_safety` keeps only the redaction contract: the
  credential filter, the token patterns, and the chatty-HTTP-logger muting. The
  dependency runs from configuration to redaction, so anything that logs is
  protected whether or not it knows this module exists.
- An event name is machine-readable and never carries a value; values are
  keyword fields. That split is what makes the journal queryable, and it is
  also the privacy mechanism: a field is added deliberately, while an
  interpolated message absorbs whatever the author had in scope.
- Levels carry fixed meanings. `WARNING` is a degraded or rejected outcome the
  system handled itself, `ERROR` is a failure needing the owner, and `CRITICAL`
  is reserved for a condition that ends the process or puts authoritative data
  in doubt.
- Correlation comes from a `contextvars` binding, so identifiers follow the
  operation rather than the process and concurrent loops cannot borrow each
  other's.
- Records carry identifiers, stable codes, enum values, counts, versions, and
  durations. Owner content and credentials never enter a field.

Standard error is the destination because several commands print
machine-readable JSON on standard out as their result contract; mixing the two
would break a pipeline.

## Alternatives considered

- **Keep formatted text lines.** Cheapest, and it is what already existed. It
  cannot answer an aggregate question, and it has no place to put a correlation
  identifier that does not also invite owner text into the message.
- **Adopt a structured-logging library such as `structlog`.** It would supply
  the processor pipeline and context binding directly. Rejected because the
  requirement here is narrow, the dependency would sit on the privacy-critical
  path, and the redaction filter and formatter are about a hundred lines
  against a library whose defaults would have to be audited and pinned anyway.
- **Send diagnostics to Langfuse instead.** Rejected by AD-002: daily operation
  must work when Langfuse is stopped, and traces are supporting records rather
  than the operational journal. Tracing remains complementary.
- **Emit metrics rather than events.** A counter would answer "how many dead
  letters" but not "which update, and why". Counting is derivable from
  structured events; the reverse is not.

## Implementation notes

All nine entry points call `configure_logging()` before any work that can fail;
six of them configured nothing before. The gateway binds its bot key and
environment once per instance and each of its two loops binds its own name,
because a `contextvars` binding does not cross into a thread the operation did
not start. The worker binds the inbox and update identifiers around the whole
claim, so records emitted by dispatch, the model boundary, persistence, and
rendering carry them without being passed the values.

Failures are recorded at their funnels rather than at each caller: `_failure`
in the Bot API transport classifies every rejection once, `_problem` in the web
adapter records every error response the API returns, `_store` in the receipt
archive reports every one of its twenty validation refusals, and the finance
service logs at the two methods every mutation passes through. Redaction is
applied to messages, to structured fields, and — because a traceback does not
exist until a formatter builds one — to the rendered exception text.

The profile-settings slice adds no content-bearing log field: bounded read failures continue through `_problem`, while locale, currency, timezone, source names, URLs, tokens, and private paths are never journal fields.

The merchant and period-comparison slice adds no analytics event or content-bearing field. Merchant labels, descriptions, signed amounts, period totals, deltas, and percentages stay out of the journal; bounded HTTP refusals continue through `_problem` with only stable codes and request-safe metadata.

Documented privacy contracts are honored rather than restated: `BotIdentity`
declares its bot id as never emitted by diagnostics and the transport therefore
records only that the credential validated; the model boundary logs the
contract name and the failing field paths, never the completion or the owner
message.

Seventeen unit tests cover the record shape, context propagation and isolation,
field redaction, traceback redaction, reserved-key collisions, the crash
handlers, and reconfiguration.

## Consequences

- Every process writes JSON to standard error by default. An operator reading
  a terminal sets `LOG_FORMAT=text`, which carries the same fields as
  `key=value` pairs.
- `LOG_LEVEL=DEBUG` adds per-step records — poll batches, graph invocations,
  repository writes — that the default level omits.
- A new failure path must add its record in the same change. A failure the
  journal cannot explain is an incomplete implementation.
- The journal is not authoritative and is not backed up. It explains behavior;
  PostgreSQL owns state and the audit trail.
- Redaction is a last line of defence against credentials only. It cannot
  recognize owner text, so keeping content out of fields stays a discipline
  enforced by review, not by the filter.
