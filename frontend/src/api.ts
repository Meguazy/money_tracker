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

export type ExpenseStatus =
  | "draft"
  | "needs_review"
  | "posted"
  | "voided"
  | "discarded";

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
