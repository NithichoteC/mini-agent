import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react"
import { AssistantRuntimeProvider } from "@assistant-ui/react"
import {
  FilePlusIcon,
  FolderPlusIcon,
  LibraryIcon,
  MoonIcon,
  NetworkIcon,
  NotebookTextIcon,
  SunIcon,
} from "lucide-react"
import { toast } from "sonner"

import { useAgent } from "@/lib/use-agent"
import { useWorkspace } from "@/lib/use-workspace"
import { nameOf, parentOf, type TreeNode } from "@/lib/workspace"
import { useTheme } from "@/components/theme-provider"
import { Thread } from "@/components/assistant-ui/elements/thread.aui"
import { MessageFooter, RetryContext } from "@/components/message-footer"
import { MetersButton } from "@/components/meters"
import { SessionMenu } from "@/components/session-menu"
import { SettingsDialog } from "@/components/settings-dialog"
import { ToolCard, ToolContext } from "@/components/tool-card"
import { TraceSheet } from "@/components/trace-sheet"
import { FileTree } from "@/components/workspace/file-tree"
import { GraphView } from "@/components/workspace/graph-view"
import { NotePane } from "@/components/workspace/note-pane"
import {
  DeleteDialog,
  NameDialog,
  type NameAsk,
} from "@/components/workspace/workspace-dialogs"
import { Button } from "@/components/ui/button"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar"
import { Toaster } from "@/components/ui/sonner"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

const WHOLE = "__whole__" // Select items cannot have "" as a value

const toastError = (message: string) => toast.error(message)

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
        Ask about your notes, or have them written
      </p>
      <p className="text-sm text-muted-foreground">
        The agent searches the workspace (or the folder chosen above), cites the
        note and heading it read, and asks before it changes a note. A separate
        reviewer checks answers that used a tool.
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

function folders(node: TreeNode | null): string[] {
  if (!node || node.type !== "folder") return []
  return [
    ...(node.path ? [node.path] : []),
    ...(node.children ?? []).flatMap(folders),
  ]
}

export function App() {
  const ws = useWorkspace(toastError)
  const [scope, setScope] = useState("")
  const agent = useAgent({ scope, onFileChanged: ws.onFileChanged })
  const [view, setView] = useState<"note" | "graph">("note")
  const [ask, setAsk] = useState<NameAsk | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const tools = useMemo(
    () => ({ parts: agent.toolParts, approve: agent.approve }),
    [agent.toolParts, agent.approve]
  )
  const allFolders = useMemo(() => folders(ws.tree), [ws.tree])
  const { loadGraph } = ws

  const { error, setError } = agent
  useEffect(() => {
    if (error) {
      toast.error(error)
      setError(null)
    }
  }, [error, setError])

  useEffect(() => {
    if (view === "graph") loadGraph(scope)
  }, [view, scope, loadGraph])

  const askCreate = useCallback(
    (parent: string, kind: "file" | "folder") =>
      setAsk({
        title: kind === "file" ? "New note" : "New folder",
        description: `In ${parent || "the workspace root"}. A note's name becomes its title and the name [[links]] use.`,
        label: "Name",
        initial: "",
        submit: "Create",
        onDone: (name) => ws.create(parent, name, kind),
      }),
    [ws]
  )
  const askRename = useCallback(
    (path: string) =>
      setAsk({
        title: `Rename ${nameOf(path)}`,
        description: `Stays in ${parentOf(path) || "the workspace root"}; drag it in the tree to move it. Search and the graph follow the rename.`,
        label: "New name",
        initial: nameOf(path).replace(/\.(md|txt)$/, ""),
        submit: "Rename",
        onDone: (name) => ws.rename(path, name),
      }),
    [ws]
  )

  return (
    <TooltipProvider>
      <SidebarProvider>
        <Sidebar>
          <SidebarHeader>
            <div className="flex items-center gap-2 px-2 py-1.5">
              <LibraryIcon />
              <div className="flex min-w-0 flex-1 flex-col leading-tight">
                <span className="text-sm font-semibold">AI-Blue</span>
                <span className="truncate text-xs text-muted-foreground">
                  notes · agent · knowledge graph
                </span>
              </div>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="New note"
                    onClick={() => askCreate("", "file")}
                  >
                    <FilePlusIcon />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>New note</TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label="New folder"
                    onClick={() => askCreate("", "folder")}
                  >
                    <FolderPlusIcon />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>New folder</TooltipContent>
              </Tooltip>
            </div>
            <ToggleGroup
              type="single"
              variant="outline"
              size="sm"
              className="w-full"
              value={view}
              onValueChange={(v) => v && setView(v as "note" | "graph")}
            >
              <ToggleGroupItem value="note" className="flex-1">
                <NotebookTextIcon />
                Notes
              </ToggleGroupItem>
              <ToggleGroupItem value="graph" className="flex-1">
                <NetworkIcon />
                Graph
              </ToggleGroupItem>
            </ToggleGroup>
          </SidebarHeader>
          <SidebarContent className="overflow-hidden">
            <FileTree
              tree={ws.tree}
              selected={ws.open?.path ?? null}
              onOpen={(path) => {
                setView("note")
                ws.openNote(path)
              }}
              onCreate={askCreate}
              onRename={askRename}
              onMove={ws.move}
              onDelete={setDeleting}
              onScope={setScope}
            />
          </SidebarContent>
        </Sidebar>

        <SidebarInset className="h-svh min-w-0">
          <ResizablePanelGroup orientation="horizontal">
            <ResizablePanel defaultSize="58%" minSize="30%" className="min-w-0">
              <div className="flex h-full min-h-0 flex-col">
                {view === "note" ? (
                  <NotePane
                    open={ws.open}
                    draft={ws.draft}
                    dirty={ws.dirty}
                    conflict={ws.conflict}
                    backlinks={ws.backlinks}
                    onEdit={ws.edit}
                    onSave={() => ws.save()}
                    onClose={ws.close}
                    onResolve={ws.resolve}
                    onOpenNote={ws.openNote}
                    onOpenLink={ws.openLink}
                    onNew={() => askCreate("", "file")}
                  />
                ) : (
                  <GraphView
                    data={ws.graph}
                    onOpenNote={(path) => {
                      setView("note")
                      ws.openNote(path)
                    }}
                    onBuild={() => ws.buildGraph(scope)}
                    building={ws.building}
                    progress={ws.progress}
                  />
                )}
              </div>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize="42%" minSize="25%" className="min-w-0">
              <div className="flex h-full min-h-0 flex-col">
                <header className="flex h-12 shrink-0 items-center gap-1 border-b px-2">
                  <SidebarTrigger />
                  <SessionMenu
                    sessions={agent.sessions}
                    current={agent.sessionId}
                    busy={agent.running}
                    onOpen={agent.open}
                    onNew={agent.newChat}
                  />
                  <MetersButton meters={agent.meters} />
                  <TraceSheet
                    sessionId={agent.sessionId}
                    version={agent.running ? 0 : 1}
                  />
                  <ThemeToggle />
                  <SettingsDialog disabled={agent.running} />
                </header>
                <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
                  <span className="text-xs text-muted-foreground">
                    Search in
                  </span>
                  <Select
                    value={scope || WHOLE}
                    onValueChange={(v) => setScope(v === WHOLE ? "" : v)}
                  >
                    <SelectTrigger size="sm" className="min-w-0 flex-1">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectGroup>
                        <SelectItem value={WHOLE}>
                          The whole workspace
                        </SelectItem>
                        {allFolders.map((f) => (
                          <SelectItem key={f} value={f}>
                            {f}/
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    </SelectContent>
                  </Select>
                </div>
                <main className="min-h-0 flex-1">
                  <AssistantRuntimeProvider runtime={agent.runtime}>
                    <ToolContext.Provider value={tools}>
                      <RetryContext.Provider value={agent.retry}>
                        <Thread components={COMPONENTS} />
                      </RetryContext.Provider>
                    </ToolContext.Provider>
                  </AssistantRuntimeProvider>
                </main>
              </div>
            </ResizablePanel>
          </ResizablePanelGroup>
        </SidebarInset>
      </SidebarProvider>
      <NameDialog ask={ask} onClose={() => setAsk(null)} />
      <DeleteDialog
        path={deleting}
        onClose={() => setDeleting(null)}
        onConfirm={(path) => {
          setDeleting(null)
          ws.remove(path)
        }}
      />
      <Toaster />
    </TooltipProvider>
  )
}

export default App
