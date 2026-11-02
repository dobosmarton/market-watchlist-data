import { describe, expect, it } from "vitest";
import { pricesSchema } from "./schema";
import { lastDays, priceSpec, summary } from "./spec";

const bars = [
  { date: "2026-01-01", close: 10 },
  { date: "2026-03-01", close: 11 },
  { date: "2026-03-31", close: 12.5 },
];

describe("lastDays", () => {
  it("keeps the bars within the range of the last one", () => {
    expect(lastDays(bars, 30).map((bar) => bar.date)).toEqual(["2026-03-01", "2026-03-31"]);
  });

  it("keeps every bar for 0 and for no bars", () => {
    expect(lastDays(bars, 0)).toBe(bars);
    expect(lastDays([], 30)).toEqual([]);
  });
});

describe("summary", () => {
  it("gives the last close and the change over the bars", () => {
    expect(summary(bars, "USD")).toBe("12.50 USD on 2026-03-31 · +25.0%");
  });

  it("says when there are no prices", () => {
    expect(summary([], "USD")).toBe("no prices");
  });
});

it("plots the closes in the quote currency", () => {
  const spec = priceSpec(bars, "EUR");
  expect(spec).toMatchObject({ data: { values: bars }, encoding: { y: { title: "EUR" } } });
});

it("reads the tool result and drops columns the view does not use", () => {
  const bar = { date: "2026-01-01", close: 1, volume: 5 };
  const result = { slug: "acme", name: "Acme", ccy: "USD", bars: [bar] };
  expect(pricesSchema.parse(result).bars).toEqual([{ date: "2026-01-01", close: 1 }]);
  expect(pricesSchema.safeParse({ slug: "acme", ccy: "USD", bars: [] }).success).toBe(false);
});
