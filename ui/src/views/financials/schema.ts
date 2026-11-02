import { z } from "zod";

const amount = z.number().nullable();
const figures = { revenue: amount, op_margin_pct: amount, net_income: amount, fcf: amount };

/** The `financials` tool result: annual figures oldest first, in millions of `ccy`. */
export const financialsSchema = z.object({
  slug: z.string(),
  ccy: z.string(),
  years: z.array(z.object({ fiscal_year: z.number(), ...figures })),
  latest: z.object({ basis: z.string(), ...figures }).nullable(),
});

export type Financials = z.infer<typeof financialsSchema>;
