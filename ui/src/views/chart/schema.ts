import type { TopLevelSpec } from "vega-lite";
import { z } from "zod";

// Claude wrote the spec and the server checked its shape; Vega-Lite reports the rest when drawing
const spec = z.custom<TopLevelSpec>(
  (value) => typeof value === "object" && value !== null && !Array.isArray(value),
  "a Vega-Lite spec",
);

/** The `chart` tool result: the server has already put the query's rows into the spec's data. */
export const chartSchema = z.object({
  title: z.string().nullable(),
  rows: z.array(z.record(z.string(), z.unknown())),
  truncated: z.boolean(),
  spec,
});

export type Chart = z.infer<typeof chartSchema>;
