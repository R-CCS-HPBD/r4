import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

/**
 * Catches render errors so a single bad message cannot leave the sidebar blank.
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('R4 agent view crashed:', error, info.componentStack);
  }

  private reload = (): void => {
    window.location.reload();
  };

  /* Retry: clear the error without a full webview reload — transient payload
     races (e.g. a malformed row in one poll cycle) recover on the next
     render instead of forcing the user to rebuild the whole view. */
  private retry = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="r4-crash">
        <h3>The agent view stopped responding</h3>
        <pre className="r4-crash-detail">{error.message}</pre>
        <div className="r4-crash-actions">
          <button type="button" className="r4-btn r4-btn-primary" onClick={this.retry}>
            Retry
          </button>
          <button type="button" className="r4-btn" onClick={this.reload}>
            Reload view
          </button>
        </div>
      </div>
    );
  }
}
