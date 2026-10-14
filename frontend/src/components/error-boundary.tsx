// One broken pane must not take the whole page down: React unmounts everything on an uncaught render
// error, which shows as a blank (white, or black in dark mode) window. This shows the error in place
// of the pane, with a retry, and leaves the rest of the app working.
import { Component, type ReactNode } from "react"
import { RotateCcwIcon, TriangleAlertIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"

type Props = { name: string; children: ReactNode }
type State = { error: Error | null }

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.error(`${this.props.name} failed:`, error)
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <TriangleAlertIcon />
          </EmptyMedia>
          <EmptyTitle>The {this.props.name} could not be shown</EmptyTitle>
          <EmptyDescription>{this.state.error.message}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button variant="outline" onClick={() => this.setState({ error: null })}>
            <RotateCcwIcon data-icon="inline-start" />
            Try again
          </Button>
        </EmptyContent>
      </Empty>
    )
  }
}
