import { describe, expect, it } from "vitest";
import { FALLBACK } from "../../theme";
import type { Company } from "./schema";
import { metricSpec, robustDomain } from "./spec";
import { COLUMNS, cell, nextSort, sortCompanies } from "./table";

function company(slug: string, changes: Partial<Company> = {}): Company {
  return {
    slug,
    name: slug.toUpperCase(),
    ccy: "USD",
    price: 10,
    mcap_bn: 1,
    pe_ttm: 15,
    ev_ebitda: 8,
    fcf_yield: 5,
    nd_ebitda: 1,
    op_margin_pct: 20,
    basis: "ttm",
    ...changes,
  };
}

describe("robustDomain", () => {
  it("keeps one outlier from stretching the axis", () => {
    const [low, high] = robustDomain([10, 12, 14, 15, 16, 18, 20, 22, 25, 400]);
    expect(low).toBe(0);
    expect(high).toBeLessThan(100);
  });

  it("always includes zero", () => {
    expect(robustDomain([-5, -3, -2])[1]).toBe(0);
    expect(robustDomain([3, 4, 5])[0]).toBe(0);
  });

  it("has a default for no values", () => {
    expect(robustDomain([])).toEqual([0, 1]);
  });
});

it("draws only the companies that have the metric, largest first", () => {
  const companies = [company("a", { pe_ttm: 5 }), company("b", { pe_ttm: null }), company("c")];
  const spec = metricSpec(companies, "pe_ttm", FALLBACK);
  expect(spec.data).toEqual({
    values: [
      { name: "A", basis: "ttm", value: 5 },
      { name: "C", basis: "ttm", value: 15 },
    ],
  });
  expect(spec).toMatchObject({ encoding: { y: { sort: ["C", "A"] } } });
});

describe("table", () => {
  const companies = [company("b", { pe_ttm: null }), company("a", { pe_ttm: 30 }), company("c")];

  it("sorts by a column with missing values last", () => {
    const slugs = (key: "pe_ttm", ascending: boolean) =>
      sortCompanies(companies, { key, ascending }).map((row) => row.slug);
    expect(slugs("pe_ttm", true)).toEqual(["c", "a", "b"]);
    expect(slugs("pe_ttm", false)).toEqual(["a", "c", "b"]);
  });

  it("flips the sorted column and starts others ascending", () => {
    expect(nextSort({ key: "name", ascending: true }, "name")).toEqual({
      key: "name",
      ascending: false,
    });
    expect(nextSort({ key: "name", ascending: false }, "pe_ttm")).toEqual({
      key: "pe_ttm",
      ascending: true,
    });
  });

  it("formats numbers, adds the currency to the price, and dashes gaps", () => {
    const [name, price, , pe] = COLUMNS;
    if (!name || !price || !pe) throw new Error("columns changed");
    const row = company("b", { pe_ttm: null });
    expect([cell(row, name), cell(row, price), cell(row, pe)]).toEqual(["B", "10.00 USD", "–"]);
  });
});
