// financials: {slug, ccy, unit, years: [{fiscal_year, revenue, op_margin_pct, net_income, fcf, ...}], latest}
async function render(data) {
  const rows = data.years.map((year) => ({ ...year, label: String(year.fiscal_year) }));
  if (data.latest && data.latest.basis === "ttm") rows.push({ ...data.latest, label: "TTM" });
  const order = rows.map((row) => row.label);
  const x = { field: "label", type: "ordinal", sort: order, title: null };
  const margin = token("--color-text-warning", "#a87829");
  const top = h("div", { class: "chart" });
  const bottom = h("div", { class: "chart" });
  root.replaceChildren(header(data.slug, `millions of ${data.ccy}`), top, bottom);
  if (!rows.length) {
    root.append(h("p", { class: "muted" }, "No annual figures yet."));
    return;
  }
  await embed(top, {
    height: 180,
    data: { values: rows },
    encoding: { x },
    layer: [
      {
        mark: { type: "bar", opacity: 0.85 },
        encoding: {
          y: { field: "revenue", type: "quantitative", title: `Revenue (m ${data.ccy})` },
          tooltip: [{ field: "label", title: "Year" }, { field: "revenue", format: ",.0f" }],
        },
      },
      {
        mark: { type: "line", color: margin, point: { color: margin } },
        encoding: {
          y: { field: "op_margin_pct", type: "quantitative", title: "Op. margin %" },
          tooltip: [{ field: "label", title: "Year" }, { field: "op_margin_pct", format: ".1f" }],
        },
      },
    ],
    resolve: { scale: { y: "independent" } },
  });
  const earnings = rows.flatMap((row) => [
    { label: row.label, measure: "Net income", value: row.net_income },
    { label: row.label, measure: "FCF", value: row.fcf },
  ]);
  await embed(bottom, {
    height: 140,
    data: { values: earnings },
    mark: "bar",
    encoding: {
      x,
      xOffset: { field: "measure", type: "nominal", sort: ["Net income", "FCF"] },
      y: { field: "value", type: "quantitative", title: `m ${data.ccy}` },
      color: { field: "measure", type: "nominal", title: null, sort: ["Net income", "FCF"] },
      tooltip: [{ field: "label", title: "Year" }, { field: "measure" }, { field: "value", format: ",.0f" }],
    },
  });
}
