import { fmt } from "../../format";
import { notMeaningful } from "./ratios";
import type { Company } from "./schema";

export type SortKey = Exclude<keyof Company, "slug" | "ccy">;

export interface Column {
  key: SortKey;
  title: string;
  /** decimals for a number; text columns have none */
  digits?: number;
}

export const COLUMNS: Column[] = [
  { key: "name", title: "Company" },
  { key: "price", title: "Price", digits: 2 },
  { key: "mcap_eur_bn", title: "Mcap (€bn)", digits: 1 },
  { key: "pe_ttm", title: "P/E", digits: 1 },
  { key: "ev_ebitda", title: "EV/EBITDA", digits: 1 },
  { key: "fcf_yield", title: "FCF yld %", digits: 1 },
  { key: "nd_ebitda", title: "ND/EBITDA", digits: 1 },
  { key: "basis", title: "Basis" },
];

export interface Sort {
  key: SortKey;
  ascending: boolean;
}

/** What a column sorts by: a value that means nothing sorts with the missing ones. */
function sortValue(company: Company, key: SortKey): Company[SortKey] {
  const value = company[key];
  return notMeaningful(key, value) ? null : value;
}

/** Sorted by one column; companies without a meaningful value go last either way. */
export function sortCompanies(companies: Company[], { key, ascending }: Sort): Company[] {
  return [...companies].sort((a, b) => {
    const [x, y] = [sortValue(a, key), sortValue(b, key)];
    if (x === y) return 0;
    if (x === null) return 1;
    if (y === null) return -1;
    return (x < y ? -1 : 1) * (ascending ? 1 : -1);
  });
}

/** A click on the sorted column flips it; another column starts ascending. */
export function nextSort(sort: Sort, key: SortKey): Sort {
  return { key, ascending: key === sort.key ? !sort.ascending : true };
}

export function cell(company: Company, { key, digits }: Column): string {
  const value = company[key];
  if (notMeaningful(key, value)) return "n/m";
  if (typeof value !== "number") return value ?? "–";
  const text = fmt(value, digits);
  return key === "price" && company.ccy ? `${text} ${company.ccy}` : text;
}
