import type { ComponentType } from "react"
import {
  BanIcon,
  ChevronDownIcon,
  CircleAlertIcon,
  HourglassIcon,
  MessageSquarePlusIcon,
  MessagesSquareIcon,
  RepeatIcon,
} from "lucide-react"

import type { SessionRow } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Spinner } from "@/components/ui/spinner"

// how a session ended, when it did not end with a passing review
const ENDED: Record<string, { icon: ComponentType; label: string }> = {
  blocked: { icon: BanIcon, label: "blocked" },
  max_runs: { icon: HourglassIcon, label: "out of actions" },
  no_progress: { icon: RepeatIcon, label: "no progress" },
  llm_error: { icon: CircleAlertIcon, label: "model error" },
}

function day(iso: string) {
  const d = new Date(iso)
  const today = new Date()
  if (d.toDateString() === today.toDateString()) return "Today"
  const yesterday = new Date(today.getTime() - 86_400_000)
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday"
  return d.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  })
}

function SessionMark({ row }: { row: SessionRow }) {
  const ended = row.status ? ENDED[row.status] : undefined
  if (row.running) return <Spinner aria-label="working" />
  if (!ended) return null
  const Icon = ended.icon
  return (
    <span
      role="img"
      aria-label={ended.label}
      title={ended.label}
      className="flex text-muted-foreground"
    >
      <Icon />
    </span>
  )
}

/** The chat panel's session switcher: past sessions by day (read from the trace), and New chat. */
export function SessionMenu({
  sessions,
  current,
  busy,
  onOpen,
  onNew,
}: {
  sessions: SessionRow[]
  current: string | null
  busy: boolean
  onOpen: (id: string) => void
  onNew: () => void
}) {
  const groups = new Map<string, SessionRow[]>()
  for (const s of sessions) {
    const key = day(s.started)
    groups.set(key, [...(groups.get(key) ?? []), s])
  }
  const title = sessions.find((s) => s.id === current)?.title || "New chat"
  return (
    <div className="flex min-w-0 flex-1 items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="max-w-full min-w-0 shrink justify-start overflow-hidden"
            disabled={busy}
          >
            <MessagesSquareIcon data-icon="inline-start" />
            <span className="truncate">{title}</span>
            <ChevronDownIcon data-icon="inline-end" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="start"
          className="max-h-96 w-80 overflow-auto"
        >
          {sessions.length === 0 && (
            <DropdownMenuLabel>No sessions yet</DropdownMenuLabel>
          )}
          {[...groups].map(([label, rows]) => (
            <DropdownMenuGroup key={label}>
              <DropdownMenuLabel>{label}</DropdownMenuLabel>
              {rows.map((s) => (
                <DropdownMenuItem
                  key={s.id}
                  onSelect={() => onOpen(s.id)}
                  disabled={s.id === current}
                >
                  <span className="min-w-0 flex-1 truncate">
                    {s.title || s.id}
                  </span>
                  <SessionMark row={s} />
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <Button
        variant="ghost"
        size="icon"
        aria-label="New chat"
        disabled={busy}
        onClick={onNew}
      >
        <MessageSquarePlusIcon />
      </Button>
    </div>
  )
}
