/** Multiples whose value at or below zero (a loss, negative EBITDA or EV) means nothing. */
const MULTIPLES: ReadonlySet<string> = new Set(["pe_ttm", "ev_ebitda"]);

/** True for a multiple the view shows as "n/m" instead of a number or a bar. */
export function notMeaningful(key: string, value: unknown): boolean {
  return MULTIPLES.has(key) && typeof value === "number" && value <= 0;
}
