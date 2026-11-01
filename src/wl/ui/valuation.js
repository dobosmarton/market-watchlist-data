// valuation: {result: [{slug, name, group, ccy, price, mcap_bn, pe_ttm, ev_ebitda, fcf_yield, nd_ebitda, basis, ...}]}
const METRICS = [["pe_ttm", "P/E"], ["ev_ebitda", "EV/EBITDA"], ["fcf_yield", "FCF yield %"], ["nd_ebitda", "ND/EBITDA"], ["op_margin_pct", "Op. margin %"]];
const COLUMNS = [
  ["name", "Company", null], ["price", "Price", 2], ["mcap_bn", "Mcap (bn)", 1], ["pe_ttm", "P/E", 1],
  ["ev_ebitda", "EV/EBITDA", 1], ["fcf_yield", "FCF yld %", 1], ["nd_ebitda", "ND/EBITDA", 1], ["basis", "Basis", null],
];
let metric = "pe_ttm";
let sortKey = "name";
let ascending = true;

async function render(data) {
  const rows = data.result;
  const title = Object.fromEntries(METRICS)[metric];
  const chart = h("div", { class: "chart" });
  root.replaceChildren(
    header("Valuation", `${rows.length} companies`, segmented(METRICS, metric, (value) => { metric = value; draw(); })),
    chart,
    h("div", { class: "table-wrap" }, table(rows)),
  );
  const values = rows.filter((row) => row[metric] !== null && row[metric] !== undefined);
  const domain = robustDomain(values.map((row) => row[metric]));
  const order = [...values].sort((a, b) => b[metric] - a[metric]).map((row) => row.name);
  const x = { field: metric, type: "quantitative", title, scale: { domain, clamp: true } };
  const [low, high] = domain;
  const label = (filter, align, dx, inside = false) => ({
    transform: [{ filter }],
    mark: {
      type: "text", align, dx, fontSize: 10,
      color: inside ? token("--color-text-inverse", "#fff") : token("--color-text-secondary", "#555"),
    },
    encoding: { x, text: { field: metric, format: ".1f" } },
  });
  await embed(chart, {
    height: { step: 16 },
    data: { values },
    encoding: {
      y: { field: "name", type: "nominal", sort: order, title: null },
      tooltip: [{ field: "name" }, { field: metric, title, format: ".1f" }, { field: "basis" }],
    },
    layer: [
      { mark: { type: "bar", cornerRadiusEnd: 2 }, encoding: { x } },
      // the value at the bar's end: an outlier's bar stops at the axis edge, its label says how far
      // (clamped outliers carry their label inside the bar, clear of the axis labels)
      label({ and: [{ field: metric, gte: 0 }, { field: metric, lte: high }] }, "left", 3),
      label({ field: metric, gt: high }, "right", -3, true),
      label({ and: [{ field: metric, lt: 0 }, { field: metric, gte: low }] }, "right", -3),
      label({ field: metric, lt: low }, "left", 3, true),
    ],
  });
}

// the axis covers the middle of the values (and zero), so one outlier cannot flatten the rest
function robustDomain(numbers) {
  const sorted = [...numbers].sort((a, b) => a - b);
  if (!sorted.length) return [0, 1];
  const at = (q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
  const [low, high] = [at(0.1), at(0.9)];
  const pad = (high - low) * 0.5 || Math.abs(high) || 1;
  return [Math.min(0, Math.max(sorted[0], low - pad)), Math.max(0, Math.min(sorted.at(-1), high + pad))];
}

function table(rows) {
  const sorted = [...rows].sort((a, b) => {
    const [x, y] = [a[sortKey], b[sortKey]];
    if (x === y) return 0;
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    return (x < y ? -1 : 1) * (ascending ? 1 : -1);
  });
  const sortBy = (key) => () => { ascending = key === sortKey ? !ascending : true; sortKey = key; draw(); };
  return h("table", {},
    h("thead", {}, h("tr", {}, ...COLUMNS.map(([key, title]) =>
      h("th", { onclick: sortBy(key) }, title + (key === sortKey ? (ascending ? " ▲" : " ▼") : ""))))),
    h("tbody", {}, ...sorted.map((row) => h("tr", {}, ...COLUMNS.map(([key, , digits]) =>
      h("td", {}, digits === null ? String(row[key] ?? "–") : fmt(row[key], digits) + (key === "price" ? ` ${row.ccy}` : "")))))));
}
