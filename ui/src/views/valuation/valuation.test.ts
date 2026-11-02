import { describe, expect, it } from "vitest";
import { FALLBACK } from "../../theme";
import type { Company } from "./schema";
import { metricPoints, metricSpec, robustDomain } from "./spec";
import { COLUMNS, cell, nextSort, sortCompanies } from "./table";

function company(slug: string, changes: Partial<Company> = {}): Company {
  return {
    slug,
    name: slug.toUpperCase(),
    ccy: "USD",
    price: 10,
    mcap_eur_bn: 1,
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
  const { points } = metricPoints(companies, "pe_ttm");
  const spec = metricSpec(points, "pe_ttm", FALLBACK);
  expect(spec.data).toEqual({
    values: [
      { name: "A", basis: "ttm", value: 5 },
      { name: "C", basis: "ttm", value: 15 },
    ],
  });
  expect(spec).toMatchObject({ encoding: { y: { sort: ["C", "A"] } } });
});

describe("negative multiples", () => {
  const loss = company("a", { pe_ttm: -482, ev_ebitda: -3, fcf_yield: -2 });
  const companies = [loss, company("b", { pe_ttm: 20 })];

  it("are named as n/m instead of drawn", () => {
    const { points, notMeaningful } = metricPoints(companies, "pe_ttm");
    expect(points.map((point) => point.name)).toEqual(["B"]);
    expect(notMeaningful).toEqual(["A"]);
    expect(metricPoints(companies, "ev_ebitda").notMeaningful).toEqual(["A"]);
  });

  it("leave other ratios below zero drawn", () => {
    expect(metricPoints(companies, "fcf_yield").points.map((point) => point.value)).toEqual([
      -2, 5,
    ]);
  });

  it("read n/m in the table and sort with the missing values", () => {
    const pe = COLUMNS.find((column) => column.key === "pe_ttm");
    if (!pe) throw new Error("columns changed");
    expect(cell(loss, pe)).toBe("n/m");
    const all = [...companies, company("c", { pe_ttm: null })];
    for (const ascending of [true, false]) {
      const slugs = sortCompanies(all, { key: "pe_ttm", ascending }).map((row) => row.slug);
      expect(slugs[0]).toBe("b");
    }
  });
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
