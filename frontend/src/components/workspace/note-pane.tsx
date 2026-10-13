// The middle of the page: the open note (edit or preview), its backlinks, and the conflict banner
// when the agent changed a note that also has unsaved typing in it.
import { useState } from "react"
import {
  CircleAlertIcon,
  EyeIcon,
  FilePlusIcon,
  LinkIcon,
  PencilIcon,
  XIcon,
} from "lucide-react"

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
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { Separator } from "@/components/ui/separator"

export type NotePaneProps = {
  open: { path: string; version: number } | null
  draft: string
  dirty: boolean
  conflict: NoteFile | null
  backlinks: Backlink[]
  onEdit: (text: string) => void
  onSave: () => void
  onClose: () => void
  onResolve: (keep: "mine" | "theirs") => void
  onOpenNote: (path: string) => void
  onOpenLink: (name: string) => void
  onNew: () => void
}

export function NotePane(props: NotePaneProps) {
  const [mode, setMode] = useState<"edit" | "preview">("edit")
  const { open, draft, dirty, conflict, backlinks } = props

  if (!open)
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <FilePlusIcon />
          </EmptyMedia>
          <EmptyTitle>No note open</EmptyTitle>
          <EmptyDescription>
            Open a note from the left, or ask the agent on the right to write
            one. Link notes with [[note name]].
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

  const parts = open.path.split("/")
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b px-3">
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
        <span className="text-xs text-muted-foreground">
          {dirty ? "Unsaved" : "Saved"}
        </span>
        <ToggleGroup
          type="single"
          size="sm"
          variant="outline"
          value={mode}
          onValueChange={(v) => v && setMode(v as "edit" | "preview")}
        >
          <ToggleGroupItem value="edit" aria-label="Edit">
            <PencilIcon />
          </ToggleGroupItem>
          <ToggleGroupItem value="preview" aria-label="Preview">
            <EyeIcon />
          </ToggleGroupItem>
        </ToggleGroup>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Close note"
          onClick={props.onClose}
        >
          <XIcon />
        </Button>
      </div>

      {conflict && (
        <Alert className="m-3 w-auto">
          <CircleAlertIcon />
          <AlertTitle>This note changed while you were typing</AlertTitle>
          <AlertDescription>
            <p>
              The agent (or another tab) saved a new version. Below: what taking
              it would change in your text.
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
          <div className="shrink-0 px-3 py-2">
            <Collapsible>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm">
                  <LinkIcon data-icon="inline-start" />
                  {backlinks.length} linked mention
                  {backlinks.length === 1 ? "" : "s"}
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <ul className="flex max-h-40 flex-col gap-1 overflow-auto py-1">
                  {backlinks.map((b, i) => (
                    <li key={i} className="flex min-w-0 flex-col">
                      <Button
                        variant="link"
                        size="xs"
                        className="self-start"
                        onClick={() => props.onOpenNote(b.path)}
                      >
                        {b.path}
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
