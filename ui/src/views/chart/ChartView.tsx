import { Header } from "../../components/Header";
import { VegaChart } from "../../components/VegaChart";
import type { Chart } from "./schema";

export function ChartView({ data }: { data: Chart }) {
  const note = `${data.rows.length} rows${data.truncated ? " (truncated at 500)" : ""}`;
  return (
    <>
      <Header title={data.title ?? "Chart"} subtitle={note} />
      <VegaChart spec={data.spec} />
    </>
  );
}
