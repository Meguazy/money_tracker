# Sequencing rules and definition of done

Part of the [delivery roadmap](../roadmap.md). These rules apply to every
step rather than to one, so they live beside the steps instead of inside
any of them.

## Dependency and sequencing rules

- A later step may add a test fixture or narrow interface early only when the
  active step requires it; it must not ship later-step behavior opportunistically.
- Evaluation datasets and regression cases grow continuously, but promotion
  gates are enforced only when their target capability is implemented.
- Backup begins with the first authoritative finance data in Step 2, not as a
  final afterthought.
- Telegram follows validated text workflows; receipts follow the media/domain,
  dashboard review, and extractor experiment foundations.
- The personal V1 release does not wait for receipts. Since 2026-09-19 the
  receipt workflow is a future development that follows the released V1, and
  release hardening must not depend on it.
- A step listed under the roadmap's future developments is not active; an agent
  may not start it, or ship any of its behavior, until the owner schedules it.
- A requested scope change that invalidates ordering requires roadmap and ADR
  updates before implementation.

## Definition of done for every implementation step

- Production behavior and observable error paths are implemented.
- Unit, real-service integration, and applicable end-to-end tests pass.
- Relevant evaluation suites meet gates and record a reproducible run.
- Migrations, rollback/recovery, configuration, and security consequences are
  addressed.
- arc42, ADR status, product requirements/journeys, operations, and the step
  document are updated in the same change.
- `README.md` remains a short entry point, and all new documentation is linked
  from an index.
- Git commits use Conventional Commits with exactly one author.
