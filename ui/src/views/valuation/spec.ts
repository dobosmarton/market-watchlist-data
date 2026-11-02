import type { TopLevelSpec } from "vega-lite";
import type { Options } from "../../components/Segmented";
import type { Palette } from "../../theme";
import { notMeaningful } from "./ratios";
import type { Company } from "./schema";

export type Metric = "pe_ttm" | "ev_ebitda" | "fcf_yield" | "nd_ebitda" | "op_margin_pct";

export const METRICS: Options<Metric> = [
  ["pe_ttm", "P/E"],
  ["ev_ebitda", "EV/EBITDA"],
  ["fcf_yield", "FCF yield %"],
  ["nd_ebitda", "ND/EBITDA"],
  ["op_margin_pct", "Op. margin %"],
];

/** The axis covers the middle of the values (and zero), so one outlier cannot flatten the rest. */
export function robustDomain(numbers: number[]): [number, number] {
  const sorted = [...numbers].sort((a, b) => a - b);
  const lowest = sorted[0];
  const highest = sorted.at(-1);
  if (lowest === undefined || highest === undefined) return [0, 1];
  const at = (q: number) => sorted[Math.floor(q * (sorted.length - 1))] ?? 0;
  const [low, high] = [at(0.1), at(0.9)];
  const pad = (high - low) * 0.5 || Math.abs(high) || 1;
  return [Math.min(0, Math.max(lowest, low - pad)), Math.max(0, Math.min(highest, high + pad))];
}

export function metricTitle(metric: Metric): string {
  return METRICS.find(([key]) => key === metric)?.[1] ?? metric;
}

export interface Point {
  name: string;
  basis: string | null;
  value: number;
}

/** The bars to draw for one metric, and the companies whose value means nothing (n/m). */
export function metricPoints(
  companies: Company[],
  metric: Metric,
): { points: Point[]; notMeaningful: string[] } {
  const points: Point[] = [];
  const skipped: string[] = [];
  for (const { name, basis, [metric]: value } of companies) {
    if (value === null) continue;
    if (notMeaningful(metric, value)) skipped.push(name);
    else points.push({ name, basis, value });
  }
  return { points, notMeaningful: skipped };
}

/** One metric as a horizontal bar per company, largest first. */
export function metricSpec(values: Point[], metric: Metric, palette: Palette): TopLevelSpec {
  const title = metricTitle(metric);
  const domain = robustDomain(values.map((point) => point.value));
  const [low, high] = domain;
  const order = [...values].sort((a, b) => b.value - a.value).map((point) => point.name);
  const x = {
    field: "value",
    type: "quantitative",
    title,
    scale: { domain, clamp: true },
  } as const;
  // the value at the bar's end: an outlier's bar stops at the axis edge and its label says how
  // far, written inside the bar so it stays clear of the axis labels
  const label = (test: string, align: "left" | "right", dx: number, inside = false) => ({
    transform: [{ filter: test }],
    mark: {
      type: "text" as const,
      align,
      dx,
      fontSize: 10,
      color: inside ? palette.inverse : palette.secondary,
    },
    encoding: { x, text: { field: "value", type: "quantitative", format: ".1f" } as const },
  });
  return {
    height: { step: 16 },
    data: { values },
    encoding: {
      y: { field: "name", type: "nominal", sort: order, title: null },
      tooltip: [
        { field: "name" },
        { field: "value", type: "quantitative", title, format: ".1f" },
        { field: "basis" },
      ],
    },
    layer: [
      { mark: { type: "bar", cornerRadiusEnd: 2 }, encoding: { x } },
      label(`datum.value >= 0 && datum.value <= ${high}`, "left", 3),
      label(`datum.value > ${high}`, "right", -3, true),
      label(`datum.value < 0 && datum.value >= ${low}`, "right", -3),
      label(`datum.value < ${low}`, "left", 3, true),
    ],
  };
}
