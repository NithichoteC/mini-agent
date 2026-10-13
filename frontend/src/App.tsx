import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
  type ReactNode,
} from "react"
import { AssistantRuntimeProvider } from "@assistant-ui/react"
import {
  FolderSearchIcon,
  NetworkIcon,
  NotebookTextIcon,
  PanelLeftIcon,
  PanelRightIcon,
} from "lucide-react"
import { usePanelRef } from "react-resizable-panels"
import { toast } from "sonner"

import { useAgent } from "@/lib/use-agent"
import { useWorkspace } from "@/lib/use-workspace"
import { nameOf, parentOf, type TreeNode } from "@/lib/workspace"
import { Thread } from "@/components/assistant-ui/elements/thread.aui"
import { MessageFooter, RetryContext } from "@/components/message-footer"
import { MetersButton } from "@/components/meters"
import { SessionMenu } from "@/components/session-menu"
import { ToolCard, ToolContext } from "@/components/tool-card"
import { TraceSheet } from "@/components/trace-sheet"
import { FilesPanel } from "@/components/workspace/files-panel"
import { GraphView } from "@/components/workspace/graph-view"
import {
  NotePane,
  NoteToolbar,
  type NoteMode,
} from "@/components/workspace/note-pane"
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

function Welcome() {
  return (
    <div className="mb-6 flex flex-col gap-2 px-2">
      <p className="text-xl font-medium tracking-tight">
        Ask about your notes, or have them written
      </p>
      <p className="text-sm text-muted-foreground">
        The agent searches the workspace (or the folder chosen above), cites the
        note and heading it read, and asks before it changes a note.
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

function PanelToggle({
  side,
  open,
  onClick,
}: {
  side: "left" | "right"
  open: boolean
  onClick: () => void
}) {
  const label = `${open ? "Hide" : "Show"} ${side === "left" ? "files" : "chat"}`
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          aria-pressed={open}
          onClick={onClick}
        >
          {side === "left" ? <PanelLeftIcon /> : <PanelRightIcon />}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function Bar({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-12 shrink-0 items-center gap-2 border-b px-2">
      {children}
    </div>
  )
}

export function App() {
  const ws = useWorkspace(toastError)
  const [scope, setScope] = useState("")
  const agent = useAgent({ scope, onFileChanged: ws.onFileChanged })
  const [view, setView] = useState<"note" | "graph">("note")
  const [mode, setMode] = useState<NoteMode>("edit")
  const [ask, setAsk] = useState<NameAsk | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const filesPanel = usePanelRef()
  const chatPanel = usePanelRef()
  const [filesOpen, setFilesOpen] = useState(true)
  const [chatOpen, setChatOpen] = useState(true)
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

  const toggle = (panel: typeof filesPanel) => {
    const p = panel.current
    if (p) {
      if (p.isCollapsed()) p.expand()
      else p.collapse()
    }
  }

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
  const openNote = useCallback(
    (path: string) => {
      setView("note")
      ws.openNote(path)
    },
    [ws]
  )

  return (
    <TooltipProvider>
      <div className="h-svh bg-background">
        <ResizablePanelGroup orientation="horizontal">
          <ResizablePanel
            id="files"
            panelRef={filesPanel}
            collapsible
            defaultSize="18%"
            minSize="200px"
            maxSize="35%"
            onResize={(size) => setFilesOpen(size.inPixels > 0)}
          >
            <FilesPanel
              busy={agent.running}
              tree={ws.tree}
              selected={ws.open?.path ?? null}
              onOpen={openNote}
              onCreate={askCreate}
              onRename={askRename}
              onMove={ws.move}
              onDelete={setDeleting}
              onScope={(folder) => {
                setScope(folder)
                chatPanel.current?.expand()
              }}
            />
          </ResizablePanel>
          <ResizableHandle />

          <ResizablePanel id="main" minSize="30%" className="min-w-0">
            <div className="flex h-full min-h-0 flex-col">
              <Bar>
                <PanelToggle
                  side="left"
                  open={filesOpen}
                  onClick={() => toggle(filesPanel)}
                />
                <ToggleGroup
                  type="single"
                  size="sm"
                  variant="outline"
                  value={view}
                  onValueChange={(v) => v && setView(v as "note" | "graph")}
                >
                  <ToggleGroupItem value="note" aria-label="Notes">
                    <NotebookTextIcon />
                    Note
                  </ToggleGroupItem>
                  <ToggleGroupItem value="graph" aria-label="Graph">
                    <NetworkIcon />
                    Graph
                  </ToggleGroupItem>
                </ToggleGroup>
                {view === "note" && ws.open ? (
                  <NoteToolbar
                    path={ws.open.path}
                    dirty={ws.dirty}
                    mode={mode}
                    onMode={setMode}
                    onClose={ws.close}
                  />
                ) : (
                  <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
                    {view === "graph"
                      ? `Graph of ${scope ? `${scope}/` : "the whole workspace"}`
                      : "No note open"}
                  </span>
                )}
                <PanelToggle
                  side="right"
                  open={chatOpen}
                  onClick={() => toggle(chatPanel)}
                />
              </Bar>
              <div className="min-h-0 flex-1">
                {view === "note" ? (
                  <NotePane
                    open={ws.open}
                    mode={mode}
                    draft={ws.draft}
                    conflict={ws.conflict}
                    backlinks={ws.backlinks}
                    onEdit={ws.edit}
                    onSave={() => ws.save()}
                    onResolve={ws.resolve}
                    onOpenNote={openNote}
                    onOpenLink={ws.openLink}
                    onNew={() => askCreate("", "file")}
                  />
                ) : (
                  <GraphView
                    data={ws.graph}
                    onOpenNote={openNote}
                    onBuild={() => ws.buildGraph(scope)}
                    building={ws.building}
                    progress={ws.progress}
                  />
                )}
              </div>
            </div>
          </ResizablePanel>
          <ResizableHandle />

          <ResizablePanel
            id="chat"
            panelRef={chatPanel}
            collapsible
            defaultSize="30%"
            minSize="320px"
            maxSize="50%"
            onResize={(size) => setChatOpen(size.inPixels > 0)}
            className="min-w-0"
          >
            <div className="flex h-full min-h-0 flex-col border-s bg-background">
              <Bar>
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
              </Bar>
              <div className="flex shrink-0 items-center gap-2 border-b px-3 py-2">
                <FolderSearchIcon className="size-4 shrink-0 text-muted-foreground" />
                <Select
                  value={scope || WHOLE}
                  onValueChange={(v) => setScope(v === WHOLE ? "" : v)}
                >
                  <SelectTrigger
                    size="sm"
                    className="min-w-0 flex-1"
                    aria-label="Where the agent searches"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectItem value={WHOLE}>
                        Search the whole workspace
                      </SelectItem>
                      {allFolders.map((f) => (
                        <SelectItem key={f} value={f}>
                          Search {f}/
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
      </div>
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
