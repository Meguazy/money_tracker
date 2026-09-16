export type Money = {
  minor_units: number;
  currency: "EUR";
};

export type FundingClass = "personal_funds" | "benefit" | "other";

export type FundingSourceSpend = {
  name: string;
  funding_class: FundingClass;
  amount: Money;
};

export type BudgetPosition = {
  category_name: string;
  target: Money;
  posted_spend: Money;
  remaining: Money;
};

export type MonthlyOverview = {
  period: { year: number; month: number };
  posted_total: Money;
  funding_split: {
    personal_funds: Money;
    benefit: Money;
    other: Money;
  };
  funding_sources: FundingSourceSpend[];
  budgets: BudgetPosition[];
  unbudgeted_categories: string[];
};

export type FundingClassSplit = Record<FundingClass, Money>;

export type AdviceEvidence =
  | {
      kind: "category_spending";
      period: { year: number; month: number };
      category_name: string;
      posted_spend: Money;
      funding_split: FundingClassSplit;
      assessment_status: "spending_without_threshold";
    }
  | {
      kind: "funding_class_split";
      period: { year: number; month: number };
      funding_split: FundingClassSplit;
      funding_sources: FundingSourceSpend[];
      assessment_status: "consumption_not_balance";
    }
  | {
      kind: "budgets_at_risk";
      period: { year: number; month: number };
      positions: BudgetPosition[];
      unbudgeted_categories: string[];
      assessment_status: "budget_evidence" | "insufficient_data";
    };

export type AdviceResponse =
  | { status: "answered"; evidence: AdviceEvidence; reason: null }
  | {
      status: "needs_clarification";
      evidence: null;
      reason:
        | "category_not_found"
        | "capability_not_available"
        | "request_not_understood";
    };

export type PeriodGranularity = "day" | "week" | "month" | "year" | "custom";

export type RangeSelection =
  | { granularity: "day" | "week"; day: string }
  | { granularity: "month"; month: string }
  | { granularity: "year"; year: string }
  | { granularity: "custom"; start: string; end: string };

export type ResolvedPeriod = {
  granularity: PeriodGranularity;
  start: string;
  end: string;
  day_count: number;
  iso_year: number | null;
  iso_week: number | null;
};

export type RangeOverview = {
  period: ResolvedPeriod;
  posted_total: Money;
  funding_split: {
    personal_funds: Money;
    benefit: Money;
    other: Money;
  };
  funding_sources: FundingSourceSpend[];
  categories: Array<{
    category_id: string;
    category_name: string;
    parent_name: string | null;
    amount: Money;
  }>;
  merchants: Array<{
    merchant: string | null;
    amount: Money;
    movement_count: number;
  }>;
  movement_count: number;
  comparison: {
    current_period: ResolvedPeriod;
    previous_period: ResolvedPeriod;
    current_total: Money;
    previous_total: Money;
    delta: Money;
    percentage_change_basis_points: number | null;
    percentage_change_status: "defined" | "previous_zero";
  };
  budgets: {
    period: { year: number; month: number };
    positions: BudgetPosition[];
    unbudgeted_categories: string[];
  } | null;
};

export type ExpenseStatus =
  "draft" | "needs_review" | "posted" | "voided" | "discarded";

export type CategoryReference = {
  id: string;
  name: string;
  parent_id: string | null;
  status: "active" | "archived";
};

export type FundingSourceReference = {
  id: string;
  name: string;
  funding_class: FundingClass;
  balance_mode: "untracked" | "ledger";
  status: "active" | "archived";
  is_default: boolean;
};

export type WalletEntryType =
  | "opening"
  | "credit"
  | "debit"
  | "refund"
  | "expiry"
  | "adjustment";

export type ManagedFundingSource = FundingSourceReference & {
  version: number;
  availability: {
    booked_balance: Money;
    available_balance: Money;
    expiring: Array<{ expires_on: string; amount: Money }>;
  } | null;
  entries: Array<{
    id: string;
    entry_type: WalletEntryType;
    amount: Money;
    occurred_at: string;
    expires_on: string | null;
    expense_id: string | null;
  }>;
};

export type DefaultFundingSource = {
  id: string;
  name: string;
  funding_class: FundingClass;
  version: number;
};

export type ProfileSettings = {
  locale: string;
  timezone: string;
  currency: "EUR";
  default_funding_source: DefaultFundingSource | null;
  available_default_sources: DefaultFundingSource[];
};

export type FundingSourceManagement = {
  observed_at: string;
  sources: ManagedFundingSource[];
};

export type FundingSourceMutation = {
  id: string;
  name: string;
  status: "active" | "archived";
  is_default: boolean;
  version: number;
};

export type FundingSourceInput = {
  name: string;
  funding_class: FundingClass;
  balance_mode: "untracked" | "ledger";
  is_default: boolean;
};

export type WalletEntryInput = {
  entry_type: "opening" | "credit" | "adjustment";
  amount: Money;
  occurred_at: string;
  expires_on: string | null;
};

export type Transaction = {
  id: string;
  amount: Money;
  occurred_at: string;
  description: string;
  merchant: string | null;
  status: ExpenseStatus;
  category_allocations: Array<{
    category_id: string;
    category_name: string | null;
    amount: Money;
    provenance: string;
    evidence: string | null;
    owner_override: boolean;
    receipt_line_id: string | null;
  }>;
  payment_allocations: Array<{
    funding_source_id: string;
    funding_source_name: string | null;
    amount: Money;
  }>;
  receipt_id: string | null;
  version: number;
};

export type TransactionRegister = {
  period: { year: number; month: number };
  transactions: Transaction[];
  categories: CategoryReference[];
  funding_sources: FundingSourceReference[];
};

export type ExpenseInput = {
  amount: Money;
  occurred_at: string;
  description: string;
  merchant: string | null;
  category_allocations: Array<{
    category_id: string;
    amount: Money;
    provenance: string;
    evidence?: string | null;
    owner_override: boolean;
    receipt_line_id?: string | null;
  }>;
  payment_allocations: Array<{
    funding_source_id: string;
    amount: Money;
  }>;
  original_expense_id?: string | null;
  receipt_id?: string | null;
};

export type ExpenseMutation = {
  id: string;
  status: ExpenseStatus;
  version: number;
};

export type BudgetOverride = {
  period: { year: number; month: number };
  target: Money;
};

export type BudgetPlanEntry = {
  category_id: string;
  category_name: string;
  recurring_target: Money;
  overrides: BudgetOverride[];
  target: Money;
  posted_spend: Money;
  remaining: Money;
  version: number;
};

export type BudgetPlan = {
  period: { year: number; month: number };
  entries: BudgetPlanEntry[];
  budgetable_categories: CategoryReference[];
};

export type BudgetInput = {
  recurring_target: Money;
  overrides: BudgetOverride[];
};

export type BudgetMutation = {
  category_id: string;
  version: number;
};

export type CategoryProposal = {
  id: string;
  proposed_name: string;
  description: string;
  parent_name: string | null;
  rationale: string;
  alternative_categories: CategoryReference[];
  excluded_names: string[];
  can_repropose: boolean;
  expense: {
    id: string;
    amount: Money;
    occurred_at: string;
    description: string;
    merchant: string | null;
    status: ExpenseStatus;
    version: number;
  };
};

export type CategoryReviewQueue = {
  proposals: CategoryProposal[];
  active_categories: CategoryReference[];
};

export type ManagedCategory = {
  id: string;
  name: string;
  description: string;
  parent_id: string | null;
  parent_name: string | null;
  aliases: string[];
  status: "active" | "archived";
  seeded: boolean;
  version: number;
};

export type CategoryManagement = {
  categories: ManagedCategory[];
  warnings: Array<{ code: string; message: string }>;
};

export type CategoryMutation = {
  id: string;
  name: string;
  status: "active" | "archived";
  version: number;
};

export type ReceiptStatus = "stored" | "needs_review" | "approved" | "deleted";

export type Receipt = {
  id: string;
  media_type: "image/jpeg" | "image/png" | "image/webp";
  byte_size: number;
  stored_at: string;
  status: ReceiptStatus;
  original_filename: string | null;
  version: number;
};

export type ReceiptRegister = {
  receipts: Receipt[];
};

export type ReceiptMutation = {
  id: string;
  status: ReceiptStatus;
  version: number;
};

type ProblemDetails = {
  detail?: string;
};

let csrfTokenRequest: Promise<string> | null = null;

async function responseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let detail = "Non è stato possibile completare la richiesta.";
    try {
      const problem = (await response.json()) as ProblemDetails;
      if (typeof problem.detail === "string" && problem.detail.length > 0) {
        detail = problem.detail;
      }
    } catch {
      // The bounded fallback intentionally does not reflect an unknown body.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function loadMonthlyOverview(
  period: string,
  signal?: AbortSignal,
): Promise<MonthlyOverview> {
  const response = await fetch(
    `/api/v1/overview/monthly?period=${encodeURIComponent(period)}`,
    { signal },
  );
  return responseJson<MonthlyOverview>(response);
}

export async function loadRangeOverview(
  selection: RangeSelection,
  signal?: AbortSignal,
): Promise<RangeOverview> {
  const parameters = new URLSearchParams(selection);
  const response = await fetch(`/api/v1/overview/range?${parameters.toString()}`, {
    signal,
  });
  return responseJson<RangeOverview>(response);
}

export async function loadTransactions(
  period: string,
  signal?: AbortSignal,
): Promise<TransactionRegister> {
  const response = await fetch(
    `/api/v1/transactions?period=${encodeURIComponent(period)}`,
    { signal },
  );
  return responseJson<TransactionRegister>(response);
}

async function csrfToken(): Promise<string> {
  csrfTokenRequest ??= fetch("/api/v1/csrf")
    .then((response) => responseJson<{ token: string }>(response))
    .then(({ token }) => token)
    .catch((error: unknown) => {
      csrfTokenRequest = null;
      throw error;
    });
  return csrfTokenRequest;
}

export async function requestAdvice(message: string): Promise<AdviceResponse> {
  const token = await csrfToken();
  const response = await fetch("/api/v1/advice", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify({ message }),
  });
  return responseJson<AdviceResponse>(response);
}

async function mutate<T>(
  path: string,
  method: "POST" | "PUT",
  body: unknown,
  idempotencyKey: string = crypto.randomUUID(),
): Promise<T> {
  const token = await csrfToken();
  const response = await fetch(path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
      "X-CSRF-Token": token,
    },
    body: JSON.stringify(body),
  });
  return responseJson<T>(response);
}

function mutateExpense(
  path: string,
  method: "POST" | "PUT",
  body: unknown,
): Promise<ExpenseMutation> {
  return mutate<ExpenseMutation>(path, method, body);
}

export async function loadBudgetPlan(
  period: string,
  signal?: AbortSignal,
): Promise<BudgetPlan> {
  const response = await fetch(
    `/api/v1/budgets?period=${encodeURIComponent(period)}`,
    { signal },
  );
  return responseJson<BudgetPlan>(response);
}

export function createBudget(
  categoryId: string,
  input: BudgetInput,
): Promise<BudgetMutation> {
  return mutate<BudgetMutation>("/api/v1/budgets", "POST", {
    category_id: categoryId,
    ...input,
  });
}

export function replaceBudget(
  categoryId: string,
  input: BudgetInput & { expected_version: number },
): Promise<BudgetMutation> {
  return mutate<BudgetMutation>(
    `/api/v1/budgets/${encodeURIComponent(categoryId)}`,
    "PUT",
    input,
  );
}

export function createExpense(input: ExpenseInput): Promise<ExpenseMutation> {
  return mutateExpense("/api/v1/expenses", "POST", input);
}

export function replaceExpense(
  expenseId: string,
  input: ExpenseInput & { expected_version: number },
): Promise<ExpenseMutation> {
  return mutateExpense(
    `/api/v1/expenses/${encodeURIComponent(expenseId)}`,
    "PUT",
    input,
  );
}

export function postExpense(
  expenseId: string,
  expectedVersion: number,
): Promise<ExpenseMutation> {
  return mutateExpense(
    `/api/v1/expenses/${encodeURIComponent(expenseId)}/post`,
    "POST",
    { expected_version: expectedVersion, receipt_approved: false },
  );
}

export function cancelExpense(
  expenseId: string,
  expectedVersion: number,
): Promise<ExpenseMutation> {
  return mutateExpense(
    `/api/v1/expenses/${encodeURIComponent(expenseId)}/cancel`,
    "POST",
    { expected_version: expectedVersion },
  );
}

export async function loadCategoryManagement(
  signal?: AbortSignal,
): Promise<CategoryManagement> {
  const response = await fetch("/api/v1/categories", { signal });
  return responseJson<CategoryManagement>(response);
}

export function renameCategory(
  categoryId: string,
  name: string,
  expectedVersion: number,
): Promise<CategoryMutation> {
  return mutate<CategoryMutation>(
    `/api/v1/categories/${encodeURIComponent(categoryId)}`,
    "PUT",
    { name, expected_version: expectedVersion },
  );
}

export function archiveCategory(
  categoryId: string,
  expectedVersion: number,
): Promise<CategoryMutation> {
  return mutate<CategoryMutation>(
    `/api/v1/categories/${encodeURIComponent(categoryId)}/archive`,
    "POST",
    { expected_version: expectedVersion },
  );
}

export async function loadProfileSettings(
  signal?: AbortSignal,
): Promise<ProfileSettings> {
  const response = await fetch("/api/v1/settings", { signal });
  return responseJson<ProfileSettings>(response);
}

export async function loadFundingSources(
  signal?: AbortSignal,
): Promise<FundingSourceManagement> {
  const response = await fetch("/api/v1/funding-sources", { signal });
  return responseJson<FundingSourceManagement>(response);
}

export function createFundingSource(
  input: FundingSourceInput,
): Promise<FundingSourceMutation> {
  return mutate<FundingSourceMutation>("/api/v1/funding-sources", "POST", input);
}

export function renameFundingSource(
  sourceId: string,
  name: string,
  expectedVersion: number,
): Promise<FundingSourceMutation> {
  return mutate<FundingSourceMutation>(
    `/api/v1/funding-sources/${encodeURIComponent(sourceId)}`,
    "PUT",
    { name, expected_version: expectedVersion },
  );
}

export function archiveFundingSource(
  sourceId: string,
  expectedVersion: number,
): Promise<FundingSourceMutation> {
  return mutate<FundingSourceMutation>(
    `/api/v1/funding-sources/${encodeURIComponent(sourceId)}/archive`,
    "POST",
    { expected_version: expectedVersion },
  );
}

export function setDefaultFundingSource(
  source: DefaultFundingSource,
  previousDefault: DefaultFundingSource | null,
): Promise<FundingSourceMutation> {
  return mutate<FundingSourceMutation>(
    `/api/v1/funding-sources/${encodeURIComponent(source.id)}/default`,
    "POST",
    {
      expected_version: source.version,
      expected_previous_default_id: previousDefault?.id ?? null,
      expected_previous_default_version: previousDefault?.version ?? null,
    },
  );
}

export function addWalletEntry(
  sourceId: string,
  input: WalletEntryInput,
): Promise<{ id: string; entry_type: WalletEntryType }> {
  return mutate<{ id: string; entry_type: WalletEntryType }>(
    `/api/v1/funding-sources/${encodeURIComponent(sourceId)}/wallet-entries`,
    "POST",
    input,
  );
}

export async function loadCategoryProposals(
  signal?: AbortSignal,
): Promise<CategoryReviewQueue> {
  const response = await fetch("/api/v1/category-proposals", { signal });
  return responseJson<CategoryReviewQueue>(response);
}

function categoryReviewKey(proposalId: string): string {
  return `category-review:${proposalId}`;
}

export function approveCategoryProposal(
  proposalId: string,
): Promise<ExpenseMutation> {
  return mutate<ExpenseMutation>(
    `/api/v1/category-proposals/${encodeURIComponent(proposalId)}/approve`,
    "POST",
    {},
    categoryReviewKey(proposalId),
  );
}

export function reuseCategoryProposal(
  proposalId: string,
  categoryName: string,
): Promise<ExpenseMutation> {
  return mutate<ExpenseMutation>(
    `/api/v1/category-proposals/${encodeURIComponent(proposalId)}/use-existing`,
    "POST",
    { category_name: categoryName },
    categoryReviewKey(proposalId),
  );
}

export function cancelCategoryProposal(
  proposalId: string,
): Promise<ExpenseMutation> {
  return mutate<ExpenseMutation>(
    `/api/v1/category-proposals/${encodeURIComponent(proposalId)}/cancel`,
    "POST",
    {},
    categoryReviewKey(proposalId),
  );
}

export async function loadReceipts(
  signal?: AbortSignal,
): Promise<ReceiptRegister> {
  const response = await fetch("/api/v1/receipts", { signal });
  return responseJson<ReceiptRegister>(response);
}

function receiptReviewKey(receiptId: string, expectedVersion: number): string {
  return `receipt-review:${receiptId}:${expectedVersion}`;
}

function reviewReceipt(
  action: "review" | "approve",
  receiptId: string,
  expectedVersion: number,
): Promise<ReceiptMutation> {
  return mutate<ReceiptMutation>(
    `/api/v1/receipts/${encodeURIComponent(receiptId)}/${action}`,
    "POST",
    { expected_version: expectedVersion },
    receiptReviewKey(receiptId, expectedVersion),
  );
}

export function openReceiptReview(
  receiptId: string,
  expectedVersion: number,
): Promise<ReceiptMutation> {
  return reviewReceipt("review", receiptId, expectedVersion);
}

export function approveReceipt(
  receiptId: string,
  expectedVersion: number,
): Promise<ReceiptMutation> {
  return reviewReceipt("approve", receiptId, expectedVersion);
}

export type MessagingQueueState = {
  pending: number;
  retrying: number;
  dead_letters: number;
  oldest_at: string | null;
};

export type InboxDeadLetter = {
  inbox_id: string;
  update_id: number;
  attempt_count: number;
  replay_count: number;
  last_error_code: string | null;
  accepted_at: string;
};

export type OutboxDeadLetter = {
  outbox_id: string;
  response_kind: string;
  attempt_count: number;
  replay_count: number;
  last_error_code: string | null;
  created_at: string;
};

export type MessagingOperations = {
  environment: "production" | "test";
  owner_paired: boolean;
  next_offset: number;
  last_poll_at: string | null;
  last_observed_at: string | null;
  downtime_exceeds_update_retention: boolean;
  active_actions: number;
  inbox: MessagingQueueState;
  outbox: MessagingQueueState;
  inbox_dead_letters: InboxDeadLetter[];
  outbox_dead_letters: OutboxDeadLetter[];
};

export type MessagingRequeue = {
  id: string;
  queue: "inbox" | "outbox";
  outcome: "applied" | "replayed";
};

export async function loadMessagingOperations(
  signal?: AbortSignal,
): Promise<MessagingOperations> {
  const response = await fetch("/api/v1/messaging", { signal });
  return responseJson<MessagingOperations>(response);
}

function requeue(
  command: "messaging-replay" | "messaging-resend",
  path: string,
  itemId: string,
  observedReplayCount: number,
): Promise<MessagingRequeue> {
  return mutate<MessagingRequeue>(
    path,
    "POST",
    { expected_replay_count: observedReplayCount },
    `${command}:${itemId}:${observedReplayCount}`,
  );
}

export function replayInboxItem(
  inboxId: string,
  observedReplayCount: number,
): Promise<MessagingRequeue> {
  return requeue(
    "messaging-replay",
    `/api/v1/messaging/inbox/${encodeURIComponent(inboxId)}/replay`,
    inboxId,
    observedReplayCount,
  );
}

export function resendOutboxItem(
  outboxId: string,
  observedReplayCount: number,
): Promise<MessagingRequeue> {
  return requeue(
    "messaging-resend",
    `/api/v1/messaging/outbox/${encodeURIComponent(outboxId)}/resend`,
    outboxId,
    observedReplayCount,
  );
}
