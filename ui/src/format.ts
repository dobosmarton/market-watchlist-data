/** A number with fixed decimals in the viewer's locale; a dash when there is none. */
export function fmt(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "–";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function signed(value: number, digits = 1): string {
  return `${value >= 0 ? "+" : ""}${fmt(value, digits)}`;
}
