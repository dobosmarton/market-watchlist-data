import { useMemo, useState } from "react";
import { Header } from "../../components/Header";
import { Segmented } from "../../components/Segmented";
import { VegaChart } from "../../components/VegaChart";
import { usePalette } from "../../theme";
import type { Valuation } from "./schema";
import { METRICS, type Metric, metricPoints, metricSpec, metricTitle } from "./spec";
import { ValuationTable } from "./ValuationTable";

export function ValuationView({ data }: { data: Valuation }) {
  const companies = data.result;
  const [metric, setMetric] = useState<Metric>("pe_ttm");
  const palette = usePalette();
  const { points, notMeaningful } = useMemo(
    () => metricPoints(companies, metric),
    [companies, metric],
  );
  const spec = useMemo(() => metricSpec(points, metric, palette), [points, metric, palette]);
  return (
    <>
      <Header title="Valuation" subtitle={`${companies.length} companies`}>
        <Segmented label="Metric" options={METRICS} value={metric} onChange={setMetric} />
      </Header>
      <VegaChart spec={spec} />
      {notMeaningful.length > 0 && (
        <p className="muted note">
          n/m (negative {metricTitle(metric)}): {notMeaningful.join(", ")}
        </p>
      )}
      <ValuationTable companies={companies} />
    </>
  );
}
