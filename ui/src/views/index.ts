import { ChartView } from "./chart/ChartView";
import { chartSchema } from "./chart/schema";
import { defineView, type View } from "./defineView";
import { FinancialsView } from "./financials/FinancialsView";
import { financialsSchema } from "./financials/schema";
import { PricesView } from "./prices/PricesView";
import { pricesSchema } from "./prices/schema";
import { valuationSchema } from "./valuation/schema";
import { ValuationView } from "./valuation/ValuationView";

/** One view per MCP tool that draws a chart, named as in src/wl/mcp_server.py VIEWS. */
export const VIEWS: Record<string, View> = {
  prices: defineView(pricesSchema, PricesView),
  financials: defineView(financialsSchema, FinancialsView),
  valuation: defineView(valuationSchema, ValuationView),
  chart: defineView(chartSchema, ChartView),
};
