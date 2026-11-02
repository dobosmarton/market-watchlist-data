import {
  type App,
  applyDocumentTheme,
  applyHostFonts,
  applyHostStyleVariables,
  type McpUiHostContext,
  useApp,
} from "@modelcontextprotocol/ext-apps/react";
import type { CallToolResult } from "@modelcontextprotocol/sdk/types.js";
import { createContext, useContext, useEffect, useState } from "react";

export interface Host {
  app: App | null;
  context: McpUiHostContext | undefined;
  result: CallToolResult | null;
  error: Error | null;
}

const HostContext = createContext<Host>({
  app: null,
  context: undefined,
  result: null,
  error: null,
});

export const HostProvider = HostContext.Provider;

export function useHost(): Host {
  return useContext(HostContext);
}

/** Connect to the MCP host: its tool result, and its theme applied to the document. */
export function useHostConnection(): Host {
  const [result, setResult] = useState<CallToolResult | null>(null);
  const [context, setContext] = useState<McpUiHostContext | undefined>();
  const { app, error } = useApp({
    appInfo: { name: "watchlist", version: "1.0.0" },
    capabilities: { availableDisplayModes: ["inline", "fullscreen"] },
    onAppCreated: (created) => {
      // handlers go in before connect() so the first tool result is not missed
      created.ontoolresult = setResult;
      created.addEventListener("hostcontextchanged", () => {
        setContext(applyHost(created.getHostContext()));
      });
      created.onteardown = () => ({});
    },
  });
  useEffect(() => {
    if (app) setContext(applyHost(app.getHostContext()));
  }, [app]);
  return { app, context, result, error };
}

// the CSS variables are set before React renders with the new context, so charts read them fresh
function applyHost(context: McpUiHostContext | undefined): McpUiHostContext | undefined {
  if (context?.theme) applyDocumentTheme(context.theme);
  if (context?.styles?.variables) applyHostStyleVariables(context.styles.variables);
  if (context?.styles?.css?.fonts) applyHostFonts(context.styles.css.fonts);
  return context;
}

export function textOf(result: CallToolResult): string {
  const block = result.content.find((item) => item.type === "text");
  return block?.type === "text" ? block.text : "No data.";
}
