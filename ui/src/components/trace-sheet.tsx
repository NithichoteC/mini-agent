import { Fragment, useEffect, useState } from "react"
import { DownloadIcon, ListTreeIcon } from "lucide-react"

import { api, type TraceStep } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"

// the trace's labels are the engine's; these are the words a person reads
const DECISIONS: Record<
  string,
  { label: string; variant: "default" | "secondary" | "destructive" | "outline" }
> = {
  allow: { label: "Allowed", variant: "secondary" },
  ask_yes: { label: "You approved", variant: "default" },
  ask_always: { label: "You approved (always)", variant: "default" },
  ask: { label: "Waiting for you", variant: "outline" },
  ask_no: { label: "You denied", variant: "destructive" },
  deny: { label: "Blocked by a rule", variant: "destructive" },
  invalid: { label: "Invalid action", variant: "destructive" },
  final_answer: { label: "Answer", variant: "secondary" },
}

export function decisionVariant(decision: string | null) {
  return DECISIONS[decision ?? ""]?.variant ?? "outline"
}

export function decisionLabel(decision: string | null) {
  return DECISIONS[decision ?? ""]?.label ?? decision ?? ""
}

/** Markdown emphasis and code marks, read as plain text in a one-line summary. */
const plain = (text: string) =>
  text.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1")

function action(s: TraceStep): string {
  if (s.tool === "final_answer")
    return plain((s.args as { answer?: string } | null)?.answer ?? "")
  if (s.tool) return s.title ?? s.tool
  return plain((s.model_output || s.observation || "").split("\n")[0])
}

const kilo = (n: number) =>
  n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
const seconds = (ms: number) =>
  ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`

/** The steps grouped by the message that started them. */
function turnsOf(steps: TraceStep[]) {
  const out: { asked: TraceStep | null; steps: TraceStep[] }[] = []
  for (const s of steps) {
    if (s.step_id === "user" || s.step_id === "hint")
      out.push({ asked: s, steps: [] })
    else if (out.length) out[out.length - 1].steps.push(s)
    else out.push({ asked: null, steps: [s] })
  }
  return out
}

/** The `main.py trace <id>` table for the open session, plus transcript.json. */
export function TraceSheet({
  sessionId,
  version,
}: {
  sessionId: string | null
  version: number
}) {
  const [open, setOpen] = useState(false)
  const [steps, setSteps] = useState<TraceStep[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !sessionId) return
    api.trace(sessionId).then(
      (t) => setSteps(t.steps),
      (e) => setError(e.message)
    )
  }, [open, sessionId, version])

  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setSteps(null)
          setError(null)
        }
        setOpen(next)
      }}
    >
      <SheetTrigger asChild>
        <Button variant="ghost" size="sm" disabled={!sessionId}>
          <ListTreeIcon data-icon="inline-start" />
          Trace
        </Button>
      </SheetTrigger>
      <SheetContent className="data-[side=right]:w-full data-[side=right]:sm:max-w-3xl">
        <SheetHeader>
          <SheetTitle>Trace</SheetTitle>
          <SheetDescription>
            {sessionId} · every step the engine recorded, grouped by message,
            with who allowed each action
          </SheetDescription>
          <div>
            <Button variant="outline" size="sm" asChild>
              <a
                href={sessionId ? api.transcriptUrl(sessionId) : undefined}
                download
              >
                <DownloadIcon data-icon="inline-start" />
                transcript.json
              </a>
            </Button>
          </div>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="px-4 pb-4">
            {error && <p className="text-sm text-destructive">{error}</p>}
            {!steps && !error && (
              <div className="flex flex-col gap-2">
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-full" />
                <Skeleton className="h-8 w-3/4" />
              </div>
            )}
            {steps && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8">#</TableHead>
                    <TableHead>what happened</TableHead>
                    <TableHead className="w-36">decision</TableHead>
                    <TableHead className="w-20 text-right">time</TableHead>
                    <TableHead
                      className="w-20 text-right"
                      title="size of that model request: prompt + reply"
                    >
                      request
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {turnsOf(steps).map((turn, t) => (
                    <Fragment key={t}>
                      {turn.asked && (
                        <TableRow>
                          <TableCell colSpan={5} className="whitespace-normal">
                            <div className="flex items-baseline gap-2">
                              <Badge variant="outline">
                                {turn.asked.step_id === "hint"
                                  ? "hint"
                                  : `message ${t + 1}`}
                              </Badge>
                              <span className="line-clamp-2 font-medium break-words">
                                {turn.asked.observation.split("\n\nAttached file ")[0]}
                              </span>
                            </div>
                          </TableCell>
                        </TableRow>
                      )}
                      {turn.steps.map((s) => (
                        <TableRow key={s.id}>
                          <TableCell>
                            <span className="text-muted-foreground tabular-nums">
                              {s.run}
                            </span>
                          </TableCell>
                          <TableCell className="max-w-0 whitespace-normal">
                            <div className="line-clamp-2 break-words">
                              {s.tool ? action(s) : `Reviewer: ${action(s)}`}
                            </div>
                            {s.tool && s.tool !== "final_answer" && (
                              <div
                                className="line-clamp-2 break-words text-muted-foreground"
                                title={s.observation}
                              >
                                → {plain(s.observation)}
                              </div>
                            )}
                            {s.model && (
                              <div className="text-xs text-muted-foreground">
                                {s.model}
                              </div>
                            )}
                          </TableCell>
                          <TableCell>
                            {s.tool ? (
                              <Badge variant={decisionVariant(s.decision)}>
                                {decisionLabel(s.decision)}
                              </Badge>
                            ) : (
                              <Badge variant={s.ok ? "secondary" : "destructive"}>
                                {s.ok ? "Review" : "Error"}
                              </Badge>
                            )}
                          </TableCell>
                          <TableCell className="text-right">
                            <span className="text-muted-foreground tabular-nums">
                              {s.duration_ms ? seconds(s.duration_ms) : ""}
                            </span>
                          </TableCell>
                          <TableCell className="text-right">
                            <span className="text-muted-foreground tabular-nums">
                              {s.tokens ? kilo(s.tokens) : ""}
                            </span>
                          </TableCell>
                        </TableRow>
                      ))}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
