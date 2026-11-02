import { useMemo } from "react";
import type { Config } from "vega-lite";
import { useHost } from "./host";

/** Colours and font from the host's style variables, so charts follow light and dark mode. */
export interface Palette {
  text: string;
  secondary: string;
  inverse: string;
  grid: string;
  font: string;
  info: string;
  warning: string;
  category: string[];
}

export const FALLBACK: Palette = {
  text: "#111",
  secondary: "#555",
  inverse: "#fff",
  grid: "#8882",
  font: "system-ui, sans-serif",
  info: "#3266ad",
  warning: "#a87829",
  category: ["#3266ad", "#a87829", "#437426", "#a73d39", "#73726c"],
};

export function usePalette(): Palette {
  const { context } = useHost();
  // biome-ignore lint/correctness/useExhaustiveDependencies: a new host context means new variables
  return useMemo(readPalette, [context]);
}

function readPalette(): Palette {
  const style = getComputedStyle(document.documentElement);
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  const info = token("--color-text-info", FALLBACK.info);
  const warning = token("--color-text-warning", FALLBACK.warning);
  return {
    text: token("--color-text-primary", FALLBACK.text),
    secondary: token("--color-text-secondary", FALLBACK.secondary),
    inverse: token("--color-text-inverse", FALLBACK.inverse),
    grid: token("--color-border-tertiary", FALLBACK.grid),
    font: token("--font-sans", FALLBACK.font),
    info,
    warning,
    category: [
      info,
      warning,
      token("--color-text-success", "#437426"),
      token("--color-text-danger", "#a73d39"),
      token("--color-text-tertiary", "#73726c"),
    ],
  };
}

export function vegaConfig(palette: Palette): Config {
  const { secondary, grid } = palette;
  return {
    background: "transparent",
    font: palette.font,
    view: { stroke: null },
    axis: {
      labelColor: secondary,
      titleColor: secondary,
      gridColor: grid,
      domainColor: grid,
      tickColor: grid,
      labelAngle: 0,
    },
    axisX: { grid: false },
    legend: { labelColor: secondary, titleColor: secondary },
    title: { color: palette.text, anchor: "start" },
    range: { category: palette.category },
    mark: { color: palette.info },
  };
}
