import type { TopLevelSpec } from "vega-lite";
import type { Financials } from "./schema";

export interface Period {
  label: string;
  revenue: number | null;
  op_margin_pct: number | null;
  net_income: number | null;
  fcf: number | null;
}

const MEASURES = ["Net income", "FCF"];

/** The fiscal years, then the trailing twelve months when they are newer than the last year. */
export function periodsOf({ years, latest }: Financials): Period[] {
  const periods = years.map((year) => ({ ...year, label: String(year.fiscal_year) }));
  return latest?.basis === "ttm" ? [...periods, { ...latest, label: "TTM" }] : periods;
}

function periodAxis(periods: Period[]) {
  const order = periods.map((period) => period.label);
  return { field: "label", type: "ordinal", sort: order, title: null } as const;
}

/** Revenue bars with the operating margin as a line on its own axis. */
export function revenueSpec(periods: Period[], ccy: string, marginColor: string): TopLevelSpec {
  return {
    height: 180,
    data: { values: periods },
    encoding: { x: periodAxis(periods) },
    layer: [
      {
        mark: { type: "bar", opacity: 0.85 },
        encoding: {
          y: { field: "revenue", type: "quantitative", title: `Revenue (m ${ccy})` },
          tooltip: [
            { field: "label", title: "Year" },
            { field: "revenue", type: "quantitative", format: ",.0f" },
          ],
        },
      },
      {
        mark: { type: "line", color: marginColor, point: { color: marginColor } },
        encoding: {
          y: { field: "op_margin_pct", type: "quantitative", title: "Op. margin %" },
          tooltip: [
            { field: "label", title: "Year" },
            { field: "op_margin_pct", type: "quantitative", format: ".1f" },
          ],
        },
      },
    ],
    resolve: { scale: { y: "independent" } },
  };
}

/** Net income and free cash flow side by side for each period. */
export function earningsSpec(periods: Period[], ccy: string): TopLevelSpec {
  const values = periods.flatMap((period) => [
    { label: period.label, measure: "Net income", value: period.net_income },
    { label: period.label, measure: "FCF", value: period.fcf },
  ]);
  return {
    height: 140,
    data: { values },
    mark: "bar",
    encoding: {
      x: periodAxis(periods),
      xOffset: { field: "measure", type: "nominal", sort: MEASURES },
      y: { field: "value", type: "quantitative", title: `m ${ccy}` },
      color: { field: "measure", type: "nominal", title: null, sort: MEASURES },
      tooltip: [
        { field: "label", title: "Year" },
        { field: "measure" },
        { field: "value", type: "quantitative", format: ",.0f" },
      ],
    },
  };
}
