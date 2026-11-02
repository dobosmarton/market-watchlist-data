import { z } from "zod";

/** The `prices` tool result: daily closes in the quote currency, oldest first. */
export const pricesSchema = z.object({
  slug: z.string(),
  ccy: z.string(),
  bars: z.array(z.object({ date: z.string(), close: z.number() })),
});

export type Prices = z.infer<typeof pricesSchema>;
export type Bar = Prices["bars"][number];
