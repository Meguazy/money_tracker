# AD-008: Controlled Two-Level Taxonomy

- Status: accepted
- Implementation: domain policy, stable 10-root/14-child seed, schema,
  PostgreSQL bootstrap, pending-proposal submission, and the deterministic
  category-fit boundary implemented; owner decisions and category mutation
  planned
- Date: 2026-09-01

## Context

Useful expense analysis needs more structure than a single generic category,
but excessive subcategories make reports unreadable. New categories also
require explicit owner approval.

## Decision

Classify by spending purpose rather than merchant or purchase occasion. Seed the
ten-category hierarchy defined in the product domain document. Enforce a
maximum depth of category plus subcategory.

Before proposing a node, search active names, aliases, and semantically close
categories. Prefer a subcategory under an existing purpose. Never create a node
without explicit approval. A rejected proposal is excluded from subsequent
suggestions in the same review. Used nodes may be renamed or archived but not
hard-deleted or silently reparented.

Use soft complexity warnings above 30 active nodes or five children under one
parent. Do not seed a catch-all `Other`; unresolved allocations remain visibly
in review.

## Alternatives considered

- An unrestricted hierarchy optimizes specificity but harms readability.
- A fixed taxonomy prevents adaptation to the owner's actual spending.
- A generic fallback category suppresses useful clarification and proposals.

## Consequences

- Category IDs, not display names, anchor history and budgets.
- Merchants, brands, payment sources, and occasions require separate metadata.
- Taxonomy changes and agent proposals are auditable.

## Implementation evidence

The application now accepts only domain-valid `pending` proposals through a
narrow port. PostgreSQL validates the active same-profile root and alternatives,
rejects normalized category/alias duplicates, and commits the proposal with its
audit atomically. The service deliberately cannot approve, reject, cancel, or
create a category; those decisions require the bounded owner-review workflow
and its evaluation gates.

Roadmap Step 3 adds the versioned category-fit boundary in front of that port.
A model names categories and never identifies them, so resolution against the
profile's active taxonomy decides. Assembling a proposal checks the candidate
name, optional parent, and every examined alternative against the taxonomy; it
refuses a name that duplicates an active node or alias, a name already rejected
in the same review, and a parent that is not an active root. A duplicate is
refused rather than quietly converted into reuse, because the adequate node
exists and hiding the wrong judgment would both misreport the reason to the
owner and suppress a measurable failure. Category allocations record the
boundary version that classified them and whether the owner forced reuse.
