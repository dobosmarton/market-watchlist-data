import { useMemo, useState } from "react";
import { Header } from "../../components/Header";
import { Segmented } from "../../components/Segmented";
import { VegaChart } from "../../components/VegaChart";
import type { Prices } from "./schema";
import { lastDays, priceSpec, RANGES, summary } from "./spec";

export function PricesView({ data }: { data: Prices }) {
  const [days, setDays] = useState(0);
  const bars = useMemo(() => lastDays(data.bars, days), [data.bars, days]);
  const spec = useMemo(() => priceSpec(bars, data.ccy), [bars, data.ccy]);
  return (
    <>
      <Header title={data.name} subtitle={summary(bars, data.ccy)}>
        <Segmented label="Range" options={RANGES} value={days} onChange={setDays} />
      </Header>
      <VegaChart spec={spec} />
    </>
  );
}
