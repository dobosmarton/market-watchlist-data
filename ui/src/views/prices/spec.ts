import type { TopLevelSpec } from "vega-lite";
import type { Options } from "../../components/Segmented";
import { fmt, signed } from "../../format";
import type { Bar } from "./schema";

/** Range buttons in calendar days; 0 shows every bar. */
export const RANGES: Options<number> = [
  [30, "1M"],
  [91, "3M"],
  [365, "1Y"],
  [0, "All"],
];

const DAY_MS = 86_400_000;

export function lastDays(bars: Bar[], days: number): Bar[] {
  const last = bars.at(-1);
  if (!last || days === 0) return bars;
  const cutoff = Date.parse(last.date) - days * DAY_MS;
  return bars.filter((bar) => Date.parse(bar.date) >= cutoff);
}

/** The last close and the change over the bars shown. */
export function summary(bars: Bar[], ccy: string): string {
  const first = bars[0];
  const last = bars.at(-1);
  if (!first || !last) return "no prices";
  const close = `${fmt(last.close, 2)} ${ccy} on ${last.date}`;
  return first.close ? `${close} · ${signed((last.close / first.close - 1) * 100)}%` : close;
}

export function priceSpec(bars: Bar[], ccy: string): TopLevelSpec {
  return {
    height: 220,
    data: { values: bars },
    mark: { type: "line", strokeWidth: 1.5 },
    encoding: {
      x: { field: "date", type: "temporal", title: null },
      y: { field: "close", type: "quantitative", title: ccy, scale: { zero: false } },
      tooltip: [
        { field: "date", type: "temporal" },
        { field: "close", type: "quantitative", format: ",.2f" },
      ],
    },
  };
}
