import type { CSSProperties, ReactNode } from "react";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Message } from "./components/Message";
import { type Host, HostProvider, textOf, useHostConnection } from "./host";
import { VIEWS } from "./views";

export function App({ view }: { view: string }) {
  const host = useHostConnection();
  return (
    <HostProvider value={host}>
      <main style={padding(host)}>{body(host, view)}</main>
    </HostProvider>
  );
}

function body({ result, error }: Host, view: string): ReactNode {
  const View = VIEWS[view];
  if (error) return <Message tone="error">Could not connect to the host: {error.message}</Message>;
  if (!View) return <Message tone="error">Unknown view “{view}”.</Message>;
  if (!result) return <Message>Loading…</Message>;
  if (result.isError || !result.structuredContent) {
    return <Message tone={result.isError ? "error" : "muted"}>{textOf(result)}</Message>;
  }
  return (
    <ErrorBoundary>
      <View content={result.structuredContent} />
    </ErrorBoundary>
  );
}

function padding({ context }: Host): CSSProperties | undefined {
  const inset = context?.safeAreaInsets;
  if (!inset) return undefined;
  return {
    padding: `${12 + inset.top}px ${12 + inset.right}px ${12 + inset.bottom}px ${12 + inset.left}px`,
  };
}
