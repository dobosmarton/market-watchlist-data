import { useEffect, useRef, useState } from "react";
import embed, { type Result } from "vega-embed";
import type { TopLevelSpec } from "vega-lite";
import { usePalette, vegaConfig } from "../theme";
import { Message } from "./Message";

/** A Vega-Lite chart as wide as its card, themed from the host. Pass a memoized spec. */
export function VegaChart({ spec }: { spec: TopLevelSpec }) {
  const target = useRef<HTMLDivElement>(null);
  const palette = usePalette();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const element = target.current;
    if (!element) return;
    let view: Result | undefined;
    let current = true;
    const sized: TopLevelSpec = {
      width: "container",
      autosize: { type: "fit-x", contains: "padding" },
      ...spec,
      config: { ...vegaConfig(palette), ...spec.config },
    };
    // ast: evaluate expressions with the interpreter instead of eval (the sandbox CSP forbids it)
    embed(element, sized, { ast: true, actions: false, renderer: "svg" }).then(
      (result) => {
        if (current) view = result;
        else result.finalize();
      },
      (reason: unknown) => {
        if (current) setError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => {
      current = false;
      view?.finalize();
    };
  }, [spec, palette]);

  if (error) return <Message tone="error">Could not draw this chart: {error}</Message>;
  return <div ref={target} className="chart" />;
}
