// The open note: its toolbar (where it is, saved or not, edit / read), the text, the conflict banner when
// the agent changed a note that also has unsaved typing in it, and the notes that link here.
import {
  CircleAlertIcon,
  EyeIcon,
  FilePlusIcon,
  LinkIcon,
  PencilIcon,
  XIcon,
} from "lucide-react"

import { cn } from "@/lib/utils"
import type { Backlink } from "@/lib/use-workspace"
import type { NoteFile } from "@/lib/workspace"
import { NoteDiff } from "@/components/workspace/note-diff"
import { MarkdownPreview, NoteEditor } from "@/components/workspace/note-editor"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Separator } from "@/components/ui/separator"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

export type NoteMode = "edit" | "read"

export function NoteToolbar({
  path,
  dirty,
  mode,
  onMode,
  onClose,
}: {
  path: string
  dirty: boolean
  mode: NoteMode
  onMode: (m: NoteMode) => void
  onClose: () => void
}) {
  const parts = path.split("/")
  return (
    <>
      <Breadcrumb className="min-w-0 flex-1">
        <BreadcrumbList className="flex-nowrap">
          {parts.map((part, i) => (
            <BreadcrumbItem key={i} className="min-w-0">
              {i === parts.length - 1 ? (
                <BreadcrumbPage>
                  <span className="block truncate">
                    {part.replace(/\.md$/, "")}
                  </span>
                </BreadcrumbPage>
              ) : (
                <span className="truncate">{part}</span>
              )}
              {i < parts.length - 1 && <BreadcrumbSeparator />}
            </BreadcrumbItem>
          ))}
        </BreadcrumbList>
      </Breadcrumb>
      <span className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
        <span
          className={cn(
            "size-1.5 rounded-full",
            dirty ? "bg-muted-foreground" : "bg-graph-entity"
          )}
        />
        {dirty ? "Saving…" : "Saved"}
      </span>
      <ToggleGroup
        type="single"
        size="sm"
        variant="outline"
        value={mode}
        onValueChange={(v) => v && onMode(v as NoteMode)}
      >
        <ToggleGroupItem value="edit" aria-label="Edit">
          <PencilIcon />
        </ToggleGroupItem>
        <ToggleGroupItem value="read" aria-label="Read">
          <EyeIcon />
        </ToggleGroupItem>
      </ToggleGroup>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Close note"
        onClick={onClose}
      >
        <XIcon />
      </Button>
    </>
  )
}

export type NotePaneProps = {
  open: { path: string; version: number } | null
  mode: NoteMode
  draft: string
  conflict: NoteFile | null
  backlinks: Backlink[]
  onEdit: (text: string) => void
  onSave: () => void
  onResolve: (keep: "mine" | "theirs") => void
  onOpenNote: (path: string) => void
  onOpenLink: (name: string) => void
  onNew: () => void
}

export function NotePane(props: NotePaneProps) {
  const { open, mode, draft, conflict, backlinks } = props

  if (!open)
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FilePlusIcon />
          </EmptyMedia>
          <EmptyTitle>No note open</EmptyTitle>
          <EmptyDescription>
            Open a note on the left, or ask the agent on the right to write one.
            Link notes with [[note name]].
          </EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button onClick={props.onNew}>
            <FilePlusIcon data-icon="inline-start" />
            New note
          </Button>
        </EmptyContent>
      </Empty>
    )

  return (
    <div className="flex h-full min-h-0 flex-col">
      {conflict && (
        <div className="shrink-0 px-6 pt-4">
          <Alert>
            <CircleAlertIcon />
            <AlertTitle>This note changed while you were typing</AlertTitle>
            <AlertDescription>
              <p>
                The agent (or another tab) saved a new version. Below: what
                taking it would change in your text.
              </p>
              <NoteDiff
                before={draft}
                after={conflict.content}
                path={open.path}
              />
              <div className="flex gap-2">
                <Button size="sm" onClick={() => props.onResolve("theirs")}>
                  Take the new version
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => props.onResolve("mine")}
                >
                  Keep mine
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        </div>
      )}

      <div className="min-h-0 flex-1">
        {mode === "edit" ? (
          <NoteEditor
            value={draft}
            version={open.version}
            onChange={props.onEdit}
            onSave={props.onSave}
          />
        ) : (
          <MarkdownPreview value={draft} onOpenLink={props.onOpenLink} />
        )}
      </div>

      {backlinks.length > 0 && (
        <>
          <Separator />
          <div className="shrink-0 px-4 py-1.5">
            <Collapsible>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm">
                  <LinkIcon data-icon="inline-start" />
                  {backlinks.length} linked mention
                  {backlinks.length === 1 ? "" : "s"}
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul className="flex max-h-44 flex-col gap-2 overflow-auto px-2 py-2">
                  {backlinks.map((b, i) => (
                    <li key={i} className="flex min-w-0 flex-col">
                      <Button
                        variant="link"
                        size="xs"
                        className="self-start"
                        onClick={() => props.onOpenNote(b.path)}
                      >
                        {b.path.replace(/\.md$/, "")}
                      </Button>
                      <span className="truncate text-xs text-muted-foreground">
                        {b.text}
                      </span>
                    </li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          </div>
        </>
      )}
    </div>
  )
}
