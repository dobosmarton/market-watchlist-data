import { useMemo } from "react";
import { Header } from "../../components/Header";
import { Message } from "../../components/Message";
import { VegaChart } from "../../components/VegaChart";
import { usePalette } from "../../theme";
import type { Financials } from "./schema";
import { earningsSpec, periodsOf, revenueSpec } from "./spec";

export function FinancialsView({ data }: { data: Financials }) {
  const { warning } = usePalette();
  const periods = useMemo(() => periodsOf(data), [data]);
  const revenue = useMemo(
    () => revenueSpec(periods, data.ccy, warning),
    [periods, data.ccy, warning],
  );
  const earnings = useMemo(() => earningsSpec(periods, data.ccy), [periods, data.ccy]);
  return (
    <>
      <Header title={data.slug} subtitle={`millions of ${data.ccy}`} />
      {periods.length === 0 ? (
        <Message>No annual figures yet.</Message>
      ) : (
        <>
          <VegaChart spec={revenue} />
          <VegaChart spec={earnings} />
        </>
      )}
    </>
  );
}
