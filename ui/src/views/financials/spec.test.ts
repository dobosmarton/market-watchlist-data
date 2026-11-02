import { describe, expect, it } from "vitest";
import type { Financials } from "./schema";
import { earningsSpec, periodsOf, revenueSpec } from "./spec";

const figures = { revenue: 100, op_margin_pct: 12.5, net_income: 9, fcf: 11 };
const data: Financials = {
  slug: "acme",
  ccy: "USD",
  years: [
    { fiscal_year: 2024, ...figures },
    { fiscal_year: 2025, ...figures, fcf: null },
  ],
  latest: { basis: "ttm", ...figures, revenue: 105 },
};

describe("periodsOf", () => {
  it("adds the trailing twelve months after the years", () => {
    expect(periodsOf(data).map((period) => period.label)).toEqual(["2024", "2025", "TTM"]);
  });

  it("leaves out a latest that is just the last year", () => {
    const annual = { ...data, latest: { basis: "annual", ...figures } };
    expect(periodsOf(annual).map((period) => period.label)).toEqual(["2024", "2025"]);
  });
});

it("orders the periods on the axis as given", () => {
  const spec = revenueSpec(periodsOf(data), "USD", "#a87829");
  expect(spec).toMatchObject({ encoding: { x: { sort: ["2024", "2025", "TTM"] } } });
});

it("puts net income and FCF side by side, keeping missing values as gaps", () => {
  const spec = earningsSpec(periodsOf(data), "USD");
  expect(spec.data).toMatchObject({
    values: expect.arrayContaining([{ label: "2025", measure: "FCF", value: null }]),
  });
});
