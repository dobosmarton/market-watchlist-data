"""Map US-GAAP and IFRS tags onto one small set of concepts.

Order matters: the first tag a company reports for a period wins. Extend a list when a
company uses a different tag; ``wl unmapped <slug>`` lists candidates.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

type Kind = Literal["flow", "instant"]


class Concept(StrEnum):
    REVENUE = "revenue"
    OPERATING_INCOME = "op_income"
    NET_INCOME = "net_income"
    OPERATING_CASH_FLOW = "cfo"
    CAPEX = "capex"
    DEPRECIATION = "dna"
    CASH = "cash"
    DEBT_TOTAL = "debt_total"
    DEBT_NONCURRENT = "debt_noncurrent"
    DEBT_CURRENT = "debt_current"
    SHARES_DILUTED = "shares_diluted"


# Balance-sheet values at the fiscal year end. Everything else, including the weighted
# average share count, is a flow reported over the fiscal year.
INSTANTS = frozenset(
    {Concept.CASH, Concept.DEBT_TOTAL, Concept.DEBT_NONCURRENT, Concept.DEBT_CURRENT}
)


def kind(concept: Concept) -> Kind:
    return "instant" if concept in INSTANTS else "flow"


TAGS: dict[Concept, list[tuple[str, str]]] = {
    Concept.REVENUE: [
        ("us-gaap", "Revenues"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "SalesRevenueNet"),
        ("ifrs-full", "Revenue"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
    ],
    Concept.OPERATING_INCOME: [
        ("us-gaap", "OperatingIncomeLoss"),
        ("ifrs-full", "ProfitLossFromOperatingActivities"),
    ],
    Concept.NET_INCOME: [
        ("us-gaap", "NetIncomeLoss"),
        ("ifrs-full", "ProfitLossAttributableToOwnersOfParent"),
        ("ifrs-full", "ProfitLoss"),
    ],
    Concept.OPERATING_CASH_FLOW: [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
        ("ifrs-full", "CashFlowsFromUsedInOperatingActivities"),
    ],
    Concept.CAPEX: [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipment"),
    ],
    Concept.DEPRECIATION: [
        ("us-gaap", "DepreciationDepletionAndAmortization"),
        ("us-gaap", "DepreciationAndAmortization"),
        ("us-gaap", "DepreciationAmortizationAndAccretionNet"),
        ("ifrs-full", "DepreciationAndAmortisationExpense"),
        (
            "ifrs-full",
            "DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
        ),
    ],
    Concept.CASH: [
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("us-gaap", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
        ("ifrs-full", "CashAndCashEquivalents"),
    ],
    Concept.DEBT_TOTAL: [
        ("us-gaap", "LongTermDebt"),
        ("us-gaap", "DebtInstrumentCarryingAmount"),
        ("ifrs-full", "Borrowings"),
    ],
    Concept.DEBT_NONCURRENT: [
        ("us-gaap", "LongTermDebtNoncurrent"),
        ("us-gaap", "ConvertibleNotesPayableNoncurrent"),
        ("ifrs-full", "NoncurrentPortionOfNoncurrentBorrowings"),
        ("ifrs-full", "LongtermBorrowings"),
    ],
    Concept.DEBT_CURRENT: [
        ("us-gaap", "LongTermDebtCurrent"),
        ("us-gaap", "DebtCurrent"),
        ("ifrs-full", "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"),
        ("ifrs-full", "ShorttermBorrowings"),
    ],
    Concept.SHARES_DILUTED: [
        ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
        ("ifrs-full", "AdjustedWeightedAverageShares"),
        ("ifrs-full", "WeightedAverageShares"),
    ],
}


def concept_rows() -> list[tuple[str, str, str, int, str]]:
    """(concept, taxonomy, tag, priority, kind) rows for the ``concept_map`` table."""
    return [
        (concept.value, taxonomy, tag, priority, kind(concept))
        for concept, tags in TAGS.items()
        for priority, (taxonomy, tag) in enumerate(tags)
    ]
