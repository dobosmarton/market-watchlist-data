import { type ComponentType, useMemo } from "react";
import { prettifyError, type ZodType } from "zod";
import { Message } from "../components/Message";

export type View = ComponentType<{ content: unknown }>;

/** A view that checks the tool's structured result against its schema before drawing it. */
export function defineView<T>(schema: ZodType<T>, Draw: ComponentType<{ data: T }>): View {
  return function ParsedView({ content }) {
    const parsed = useMemo(() => schema.safeParse(content), [content]);
    if (!parsed.success) {
      return <Message tone="error">Unexpected tool result: {prettifyError(parsed.error)}</Message>;
    }
    return <Draw data={parsed.data} />;
  };
}
