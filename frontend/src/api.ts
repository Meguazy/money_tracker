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

type ProblemDetails = {
  detail?: string;
};

export async function loadMonthlyOverview(
  period: string,
  signal?: AbortSignal,
): Promise<MonthlyOverview> {
  const response = await fetch(
    `/api/v1/overview/monthly?period=${encodeURIComponent(period)}`,
    { signal },
  );
  if (!response.ok) {
    let detail = "Non è stato possibile caricare i dati.";
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
  return (await response.json()) as MonthlyOverview;
}
