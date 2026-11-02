import type { ReactNode } from "react";

export function Message({
  tone = "muted",
  children,
}: {
  tone?: "muted" | "error";
  children: ReactNode;
}) {
  return <p className={tone}>{children}</p>;
}
