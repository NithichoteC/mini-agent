import { createContext, useContext } from "react"
import { useAuiState } from "@assistant-ui/react"
import { CircleAlertIcon, PaperclipIcon, RotateCcwIcon } from "lucide-react"

import type { Part } from "@/lib/api"
import { STOPPED } from "@/lib/use-agent"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"

type Custom = { notes?: Part[]; status?: string; kind?: string; parts?: Part[] }

/** App provides "send the last message again"; the error card offers it. */
export const RetryContext = createContext<() => void>(() => {})

function Verdict({ part }: { part: Extract<Part, { type: "review" }> }) {
  const [first, ...rest] = part.text.trim().split("\n")
  const word =
    first
      .replace(/^VERDICT:\s*/i, "")
      .trim()
      .split(/\s/)[0]
      ?.toUpperCase() || "?"
  const variant =
    word === "PASS" ? "secondary" : word === "FAIL" ? "destructive" : "outline"
  return (
    <HoverCard openDelay={100}>
      <HoverCardTrigger asChild>
        <Badge variant={variant} tabIndex={0}>
          {word === "PASS"
            ? "Checked by the reviewer"
            : word === "FAIL"
              ? "Reviewer: not done yet"
              : word === "BLOCKED"
                ? "Reviewer: cannot be done"
                : `Reviewer: ${word}`}
        </Badge>
      </HoverCardTrigger>
      <HoverCardContent className="w-96">
        <div className="flex flex-col gap-2 text-sm">
          <p className="font-medium">{first}</p>
          {rest.join("\n").trim() && (
            <p className="whitespace-pre-wrap text-muted-foreground">
              {rest.join("\n").trim()}
            </p>
          )}
          {part.model && (
            <p className="text-xs text-muted-foreground">{part.model}</p>
          )}
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}

/** What the agent is doing right now, from the steps that have arrived: the gaps between steps are model
calls of several seconds, and a bare spinner there reads as a stuck page. */
function status(parts: Part[]): string {
  const last = parts.at(-1)
  if (!last) return "Reading your question…"
  if (last.type === "tool" && last.approval && !last.approval.answered)
    return "Waiting for your approval"
  if (last.type === "tool" && last.state.status === "pending")
    return `${last.title}…`
  if (last.type === "text") return "The reviewer is checking the answer…"
  if (last.type === "review") return "Working on what the reviewer asked…"
  return "Deciding the next step…"
}

function StatusLine({ parts }: { parts: Part[] }) {
  return (
    <div
      className="flex items-center gap-2 text-sm text-muted-foreground"
      aria-live="polite"
    >
      <Spinner />
      <span className="truncate">{status(parts)}</span>
    </div>
  )
}

/** Under each message: the reviewer's verdicts, why the agent stopped, attachment notes. */
export function MessageFooter() {
  const role = useAuiState((s) => s.message.role)
  const custom = useAuiState((s) => s.message.metadata.custom) as Custom
  const attachments = useAuiState((s) =>
    s.message.role === "user" ? s.message.attachments : undefined
  )
  const isLast = useAuiState((s) => s.message.isLast)
  const retry = useContext(RetryContext)

  if (role === "user") {
    const notes = (attachments ?? [])
      .map((a) => (a.content?.[0] as { text?: string } | undefined)?.text)
      .filter((t): t is string => Boolean(t) && t !== "uploading")
    return (
      <div className="col-span-full col-start-2 flex flex-col items-end gap-1">
        {custom?.kind === "hint" && (
          <Badge variant="outline">hint for the agent</Badge>
        )}
        {notes.map((n) => (
          <span
            key={n}
            className="flex items-center gap-1 text-xs text-muted-foreground"
          >
            <PaperclipIcon className="size-3" />
            {n}
          </span>
        ))}
      </div>
    )
  }

  const notes = custom?.notes ?? []
  const stopped = custom?.status && STOPPED[custom.status]
  return (
    <>
      {custom?.status === "running" && (
        <StatusLine parts={custom.parts ?? []} />
      )}
      {notes.map((p, i) =>
        p.type === "review" ? <Verdict key={i} part={p} /> : null
      )}
      {custom?.status === "stopped" && (
        <Badge variant="outline">stopped by you</Badge>
      )}
      {stopped && (
        <Badge variant="outline">
          stopped: the agent {stopped} - your next message is sent as a hint
        </Badge>
      )}
      {notes
        .filter((p) => p.type === "error")
        .map((p, i) => (
          <Alert key={i} variant="destructive" className="my-2">
            <CircleAlertIcon />
            <AlertTitle>The turn could not finish</AlertTitle>
            <AlertDescription className="break-words">
              <div className="flex flex-col items-start gap-2">
                {p.text}
                {isLast && (
                  <Button variant="outline" size="sm" onClick={retry}>
                    <RotateCcwIcon data-icon="inline-start" />
                    Try again
                  </Button>
                )}
              </div>
            </AlertDescription>
          </Alert>
        ))}
    </>
  )
}
