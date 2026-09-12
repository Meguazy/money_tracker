# AD-008: Controlled Two-Level Taxonomy

- Status: accepted
- Implementation: domain policy, stable 10-root/14-child seed, schema,
  PostgreSQL bootstrap, pending-proposal submission, the deterministic
  category-fit boundary, the replay-safe owner decision service that creates a
  node, and the persistent review that pauses for the owner implemented
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
audit atomically. Submission still cannot approve, reject, cancel, or create a
category: that authority lives in a separate `CategoryReviewService` behind its
own port, because submitting a candidate changes nothing the owner has seen
while settling one can extend the taxonomy and complete a movement.

Roadmap Step 3 implements the owner decisions. Approval creates the node and
completes the movement in one transaction, so no state exists in which a
category was created for a movement that never received it. The node is
validated by extending the real taxonomy aggregate rather than by restating its
rules, which re-checks identifier uniqueness, the two-level depth limit, an
active parent, and unambiguous active names and aliases. Both reuse branches
resolve through active names and aliases and record that the owner forced the
classification. A re-proposal inherits every candidate already refused in the
same review and is capped at `MAX_REVIEW_PROPOSALS`, after which the review
offers existing categories and manual entry instead of another candidate.

Every branch of one review shares a single proposal-wide command key claimed
in the settlement transaction, so an identical replayed decision returns its
first result and a competing branch is refused rather than applied twice.
Cancelling ends a
review with no node and discards the draft: `voided` could not express that,
because voiding reverses a movement that already counted and stays reconciled,
while a cancelled draft never counted and has no purpose dimension. Migration
`20260903_0004` adds the `discarded` terminal state for it.

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

Roadmap Step 5 exposes the deterministic owner-decision subset through the
local dashboard. The API lists pending proposals with their stored rationale,
parent, exclusions, and existing-category shortlist, then supports approval,
active-category reuse, and cancellation through the same
`CategoryReviewService`; it does not implement a second taxonomy mutation
path. A proposal is paired server-side to the movement sharing the workflow's
trusted source reference. The browser receives neither that reference nor the
profile ID and cannot nominate a movement for settlement. The application
rechecks the association before a decision, and missing or ambiguous
correlation is refused and logged without owner content.

Generating another candidate remains in the persistent conversational workflow
and its evaluated category-fit boundary. The web process contains no model or
candidate generator; its complete manual fallback is reuse of any active
category by name, preserving this decision's owner-only creation authority and
bounded proposal loop.
