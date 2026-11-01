// chart: {title, rows, truncated, spec} (the server already put the rows into spec.data)
async function render(data) {
  const note = `${data.rows.length} rows` + (data.truncated ? " (truncated at 500)" : "");
  const chart = h("div", { class: "chart" });
  root.replaceChildren(header(data.title ?? "Chart", note), chart);
  await embed(chart, data.spec);
}
