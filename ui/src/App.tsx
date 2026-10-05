import { useEffect, useMemo, type PropsWithChildren } from "react"
import { AssistantRuntimeProvider } from "@assistant-ui/react"
import { MoonIcon, SunIcon } from "lucide-react"
import { toast } from "sonner"

import { useAgent } from "@/lib/use-agent"
import { useTheme } from "@/components/theme-provider"
import { AppSidebar } from "@/components/app-sidebar"
import { Thread } from "@/components/assistant-ui/elements/thread.aui"
import { MessageFooter, RetryContext } from "@/components/message-footer"
import { MetersButton } from "@/components/meters"
import { SettingsDialog } from "@/components/settings-dialog"
import { ToolCard, ToolContext } from "@/components/tool-card"
import { TraceSheet } from "@/components/trace-sheet"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"

function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const dark =
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches)
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Toggle theme"
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </Button>
  )
}

function Welcome() {
  return (
    <div className="mb-6 flex flex-col gap-2 px-2">
      <p className="text-2xl font-medium tracking-tight">
        What should the agent do?
      </p>
      <p className="text-sm text-muted-foreground">
        It works in its own folder with the tools enabled in Settings, asks
        before anything set to "ask", and a separate reviewer checks the answer.
        Drop a file here to attach it.
      </p>
    </div>
  )
}

// every tool call is its own collapsible card, so a group needs no extra fold
function ToolGroup({ children }: PropsWithChildren) {
  return <div className="flex flex-col">{children}</div>
}

const COMPONENTS = {
  ToolFallback: ToolCard,
  ToolGroup,
  Welcome,
  Footer: MessageFooter,
}

export function App() {
  const agent = useAgent()
  const { error, setError } = agent
  const title =
    agent.sessions.find((s) => s.id === agent.sessionId)?.title ?? "New chat"
  const tools = useMemo(
    () => ({ parts: agent.toolParts, approve: agent.approve }),
    [agent.toolParts, agent.approve]
  )

  useEffect(() => {
    if (error) {
      toast.error(error)
      setError(null)
    }
  }, [error, setError])

  return (
    <TooltipProvider>
      <SidebarProvider>
        <AppSidebar
          sessions={agent.sessions}
          current={agent.sessionId}
          busy={agent.running}
          onOpen={agent.open}
          onNew={agent.newChat}
        />
        <SidebarInset className="h-svh min-w-0">
          <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
            <SidebarTrigger />
            <Separator
              orientation="vertical"
              className="data-[orientation=vertical]:h-4"
            />
            <h1 className="min-w-0 flex-1 truncate text-sm font-medium">
              {title}
            </h1>
            <MetersButton meters={agent.meters} />
            <TraceSheet
              sessionId={agent.sessionId}
              version={agent.running ? 0 : 1}
            />
            <ThemeToggle />
            <SettingsDialog disabled={agent.running} />
          </header>
          <main className="min-h-0 flex-1">
            <AssistantRuntimeProvider runtime={agent.runtime}>
              <ToolContext.Provider value={tools}>
                <RetryContext.Provider value={agent.retry}>
                  <Thread components={COMPONENTS} />
                </RetryContext.Provider>
              </ToolContext.Provider>
            </AssistantRuntimeProvider>
          </main>
        </SidebarInset>
      </SidebarProvider>
      <Toaster />
    </TooltipProvider>
  )
}

export default App
