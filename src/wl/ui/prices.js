// prices: {slug, ccy, bars: [{date, close, volume}]}
const RANGES = [[30, "1M"], [91, "3M"], [365, "1Y"], [0, "All"]];
let range = 0;

async function render(data) {
  const last = data.bars.at(-1);
  const cutoff = last && range ? new Date(Date.parse(last.date) - range * 86400000) : null;
  const bars = cutoff ? data.bars.filter((bar) => Date.parse(bar.date) >= cutoff) : data.bars;
  const first = bars[0];
  const change = first && last ? (last.close / first.close - 1) * 100 : null;
  const subtitle = last
    ? `${fmt(last.close, 2)} ${data.ccy} on ${last.date}` + (change === null ? "" : ` · ${change >= 0 ? "+" : ""}${fmt(change)}%`)
    : "no prices";
  const chart = h("div", { class: "chart" });
  root.replaceChildren(
    header(data.slug, subtitle, segmented(RANGES, range, (value) => { range = value; draw(); })),
    chart,
  );
  await embed(chart, {
    height: 220,
    data: { values: bars },
    mark: { type: "line", strokeWidth: 1.5 },
    encoding: {
      x: { field: "date", type: "temporal", title: null },
      y: { field: "close", type: "quantitative", title: data.ccy, scale: { zero: false } },
      tooltip: [{ field: "date", type: "temporal" }, { field: "close", format: ",.2f" }],
    },
  });
}
