import type { ComponentType } from "react"
import {
  BanIcon,
  BotIcon,
  CircleAlertIcon,
  HourglassIcon,
  MessageSquarePlusIcon,
  RepeatIcon,
} from "lucide-react"

import type { SessionRow } from "@/lib/api"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
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

export function AppSidebar({
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
  return (
    <Sidebar>
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <BotIcon />
          <div className="flex flex-col leading-tight">
            <span className="text-sm font-semibold">mini-agent</span>
            <span className="text-xs text-muted-foreground">
              tool agent · trace · reviewer
            </span>
          </div>
        </div>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={onNew}
              disabled={busy}
              tooltip="New chat"
            >
              <MessageSquarePlusIcon />
              New chat
            </SidebarMenuButton>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarHeader>
      <SidebarContent>
        {[...groups].map(([label, rows]) => (
          <SidebarGroup key={label}>
            <SidebarGroupLabel>{label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {rows.map((s) => (
                  <SidebarMenuItem key={s.id}>
                    <SidebarMenuButton
                      isActive={s.id === current}
                      disabled={busy && s.id !== current}
                      onClick={() => onOpen(s.id)}
                      tooltip={s.id}
                    >
                      <span className="min-w-0 flex-1 truncate">
                        {s.title || s.id}
                      </span>
                      <SessionMark row={s} />
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarFooter>
        <p className="px-2 text-xs text-muted-foreground">
          Sessions are read from the trace (sandbox/trace.db).
        </p>
      </SidebarFooter>
    </Sidebar>
  )
}
