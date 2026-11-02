import { z } from "zod";

const ratio = z.number().nullable();

export const companySchema = z.object({
  slug: z.string(),
  name: z.string(),
  ccy: z.string().nullable(),
  price: ratio,
  mcap_eur_bn: ratio,
  pe_ttm: ratio,
  ev_ebitda: ratio,
  fcf_yield: ratio,
  nd_ebitda: ratio,
  op_margin_pct: ratio,
  basis: z.string().nullable(),
});

/** The `valuation` tool result: one row per company. */
export const valuationSchema = z.object({ result: z.array(companySchema) });

export type Valuation = z.infer<typeof valuationSchema>;
export type Company = z.infer<typeof companySchema>;
