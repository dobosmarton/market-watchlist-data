import { Component, type ReactNode } from "react";
import { Message } from "./Message";

interface State {
  error: Error | null;
}

/** Shows a view's render error in place of the view instead of a blank card. */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  override state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  override render() {
    if (this.state.error) {
      return <Message tone="error">Could not draw this: {this.state.error.message}</Message>;
    }
    return this.props.children;
  }
}
