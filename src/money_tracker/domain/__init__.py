"""Framework-independent contracts for authoritative personal-finance behavior."""

from money_tracker.domain.audit import AuditEvent
from money_tracker.domain.budgets import (
    BudgetStatus,
    MonthlyBudget,
    YearMonth,
    budget_status,
)
from money_tracker.domain.categories import (
    Category,
    CategoryProposal,
    CategoryProposalStatus,
    CategoryStatus,
    Taxonomy,
    TaxonomyWarning,
    normalize_category_name,
)
from money_tracker.domain.errors import (
    DomainError,
    InvalidTransitionError,
    ReconciliationError,
    TaxonomyError,
    WalletError,
)
from money_tracker.domain.finance import (
    CategoryAllocation,
    Expense,
    ExpenseStatus,
    FundingClassSplit,
    PaymentAllocation,
    posted_category_spend,
    posted_funding_class_split,
)
from money_tracker.domain.funding import (
    BalanceMode,
    FundingClass,
    FundingSource,
    FundingSourceStatus,
    WalletEntry,
    WalletEntryType,
    require_available_balance,
    wallet_balance,
)
from money_tracker.domain.money import Money, sum_money
from money_tracker.domain.profile import Profile
from money_tracker.domain.receipts import ReceiptMetadata, ReceiptStatus
from money_tracker.domain.taxonomy_seed import TAXONOMY_VERSION, initial_taxonomy

__all__ = [
    "AuditEvent",
    "BalanceMode",
    "BudgetStatus",
    "Category",
    "CategoryAllocation",
    "CategoryProposal",
    "CategoryProposalStatus",
    "CategoryStatus",
    "DomainError",
    "Expense",
    "ExpenseStatus",
    "FundingClass",
    "FundingClassSplit",
    "FundingSource",
    "FundingSourceStatus",
    "InvalidTransitionError",
    "Money",
    "MonthlyBudget",
    "PaymentAllocation",
    "Profile",
    "ReceiptMetadata",
    "ReceiptStatus",
    "ReconciliationError",
    "TAXONOMY_VERSION",
    "Taxonomy",
    "TaxonomyError",
    "TaxonomyWarning",
    "WalletEntry",
    "WalletEntryType",
    "WalletError",
    "YearMonth",
    "budget_status",
    "initial_taxonomy",
    "normalize_category_name",
    "posted_category_spend",
    "posted_funding_class_split",
    "require_available_balance",
    "sum_money",
    "wallet_balance",
]
