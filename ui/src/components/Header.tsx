import type { ReactNode } from "react";
import { useHost } from "../host";

export function Header({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children?: ReactNode;
}) {
  const { app, context } = useHost();
  const canExpand =
    context?.availableDisplayModes?.includes("fullscreen") && context.displayMode !== "fullscreen";
  return (
    <header>
      <h1>{title}</h1>
      {subtitle && <span className="muted">{subtitle}</span>}
      <span className="spacer" />
      {children}
      {app && canExpand && (
        <button
          type="button"
          className="ghost"
          onClick={() => void app.requestDisplayMode({ mode: "fullscreen" })}
        >
          Expand
        </button>
      )}
    </header>
  );
}
