/**
 * Catch React render failures and replace a blank renderer with a recoverable,
 * data-safe explanation and reload action.
 */

import {
  Component,
  type ErrorInfo,
  type ReactNode,
} from 'react'

interface AppErrorBoundaryProps {
  children: ReactNode
}

interface AppErrorBoundaryState {
  error: Error | null
}

/** Keep a renderer failure understandable instead of leaving a blank window. */
export class AppErrorBoundary extends Component<
  AppErrorBoundaryProps,
  AppErrorBoundaryState
> {
  state: AppErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Elysia renderer failed.', error, info.componentStack)
  }

  render() {
    if (this.state.error !== null) {
      return (
        <main className="fatal-renderer" role="alert">
          <span className="fatal-renderer-mark" aria-hidden="true">✦</span>
          <p className="eyebrow">Renderer error</p>
          <h1>Elysia could not display this view.</h1>
          <p>
            Your local Chat and Memory data were not changed. Reload the
            interface, or close and reopen Elysia if the problem continues.
          </p>
          <button
            type="button"
            onClick={() => { window.location.reload() }}
          >
            Reload interface
          </button>
        </main>
      )
    }
    return this.props.children
  }
}
