import { useMemo, useState } from "react";
import { Header } from "../../components/Header";
import { Segmented } from "../../components/Segmented";
import { VegaChart } from "../../components/VegaChart";
import { usePalette } from "../../theme";
import type { Valuation } from "./schema";
import { METRICS, type Metric, metricSpec } from "./spec";
import { ValuationTable } from "./ValuationTable";

export function ValuationView({ data }: { data: Valuation }) {
  const companies = data.result;
  const [metric, setMetric] = useState<Metric>("pe_ttm");
  const palette = usePalette();
  const spec = useMemo(() => metricSpec(companies, metric, palette), [companies, metric, palette]);
  return (
    <>
      <Header title="Valuation" subtitle={`${companies.length} companies`}>
        <Segmented label="Metric" options={METRICS} value={metric} onChange={setMetric} />
      </Header>
      <VegaChart spec={spec} />
      <ValuationTable companies={companies} />
    </>
  );
}
