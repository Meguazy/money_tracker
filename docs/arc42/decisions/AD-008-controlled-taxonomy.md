# AD-008: Controlled Two-Level Taxonomy

- Status: accepted
- Implementation: domain policy, stable 10-root/14-child seed, schema, and
  PostgreSQL bootstrap implemented; owner-management services planned
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
