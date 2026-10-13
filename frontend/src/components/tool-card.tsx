import { createContext, useContext, useState, type ComponentType } from "react"
import {
  ChevronRightIcon,
  FilePenIcon,
  FileSearchIcon,
  FileTextIcon,
  FolderSearchIcon,
  GlobeIcon,
  LibraryIcon,
  SearchIcon,
  ShieldQuestionIcon,
  SquarePenIcon,
  TerminalIcon,
  WrenchIcon,
} from "lucide-react"
import type { ToolCallMessagePartProps } from "@assistant-ui/react"

import type { Part } from "@/lib/api"
import { cn } from "@/lib/utils"
import { decisionLabel, decisionVariant } from "@/components/trace-sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"

type ToolPart = Extract<Part, { type: "tool" }>

export const ToolContext = createContext<{
  parts: Map<string, Part>
  approve: (
    id: string,
    decision: "allow" | "always" | "deny",
    reason: string
  ) => Promise<void>
}>({ parts: new Map(), approve: async () => {} })

const ICONS: Record<string, ComponentType> = {
  bash: TerminalIcon,
  read: FileTextIcon,
  write: FilePenIcon,
  edit: SquarePenIcon,
  glob: FolderSearchIcon,
  grep: FileSearchIcon,
  webfetch: GlobeIcon,
  websearch: SearchIcon,
  rag_search: LibraryIcon,
}

function Block({ label, text }: { label: string; text: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      <pre className="max-h-72 overflow-auto rounded-md bg-muted p-2 font-mono text-xs break-words whitespace-pre-wrap">
        {text}
      </pre>
    </div>
  )
}

function ApprovalFooter({ part }: { part: ToolPart }) {
  const { approve } = useContext(ToolContext)
  const [reason, setReason] = useState("")
  const [busy, setBusy] = useState(false)
  const approval = part.approval!
  const answer = async (decision: "allow" | "always" | "deny") => {
    setBusy(true)
    try {
      await approve(approval.id, decision, reason)
    } finally {
      setBusy(false)
    }
  }
  if (approval.answered) {
    return (
      <CardFooter>
        <p className="text-sm text-muted-foreground">
          Answer sent - waiting for the tool.
        </p>
      </CardFooter>
    )
  }
  return (
    <CardFooter>
      <div className="flex w-full flex-wrap items-center gap-2">
        <Button size="sm" disabled={busy} onClick={() => answer("allow")}>
          Allow once
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={busy}
          onClick={() => answer("always")}
        >
          Always allow {approval.always}
        </Button>
        <Input
          className="h-8 min-w-40 flex-1"
          placeholder="Reason (optional, sent to the agent)"
          aria-label="Reason for denying"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        <Button
          size="sm"
          variant="destructive"
          disabled={busy}
          onClick={() => answer("deny")}
        >
          Deny
        </Button>
      </div>
    </CardFooter>
  )
}

/** One tool call: icon + title from tools.json, the permission decision, args and observation. */
export function ToolCard({
  toolCallId,
  toolName,
  args,
}: ToolCallMessagePartProps) {
  const { parts } = useContext(ToolContext)
  const part = parts.get(toolCallId) as ToolPart | undefined
  const pending = Boolean(part?.approval && !part.approval.answered)
  const [open, setOpen] = useState(false)
  const Icon = ICONS[toolName] ?? WrenchIcon
  const input = part?.state.input ?? args
  const decision = part?.state.decision ?? null

  return (
    <Collapsible
      open={open || pending}
      onOpenChange={setOpen}
      className="my-2 w-full"
    >
      <Card size="sm">
        <CardHeader>
          <CollapsibleTrigger className="w-full min-w-0 text-left">
            <div className="flex min-w-0 items-center gap-2">
              {pending ? <ShieldQuestionIcon /> : <Icon />}
              <CardTitle className="min-w-0 flex-1">
                <span className="block truncate font-mono">
                  {part?.title ?? toolName}
                </span>
              </CardTitle>
              {decision && (
                <Badge variant={decisionVariant(decision)}>
                  {pending ? "needs approval" : decisionLabel(decision)}
                </Badge>
              )}
              <ChevronRightIcon
                className={cn(
                  "transition-transform",
                  (open || pending) && "rotate-90"
                )}
              />
            </div>
          </CollapsibleTrigger>
          {pending && (
            <CardDescription>
              The agent wants to run this. It waits for your answer.
            </CardDescription>
          )}
        </CardHeader>
        <CollapsibleContent>
          <CardContent>
            <div className="flex flex-col gap-3">
              <Block
                label="input"
                text={
                  typeof input === "string"
                    ? input
                    : JSON.stringify(input, null, 2)
                }
              />
              {part?.state.output != null && (
                <Block label="observation" text={part.state.output} />
              )}
              {part?.state.duration_ms != null && (
                <span className="text-xs text-muted-foreground">
                  {part.state.duration_ms} ms
                </span>
              )}
            </div>
          </CardContent>
        </CollapsibleContent>
        {pending && part && <ApprovalFooter part={part} />}
      </Card>
    </Collapsible>
  )
}
