// The workspace file tree (react-arborist): folders first, drag to move, right-click for the rest.
import { useEffect, useMemo, useRef, useState } from "react"
import { Tree, type NodeRendererProps, type TreeApi } from "react-arborist"
import {
  ChevronRightIcon,
  FilePlusIcon,
  FileTextIcon,
  FolderPlusIcon,
  MessageSquareTextIcon,
  PencilIcon,
  Trash2Icon,
} from "lucide-react"

import { cn } from "@/lib/utils"
import { safeStorage } from "@/lib/url-state"
import type { TreeNode } from "@/lib/workspace"
import { Button } from "@/components/ui/button"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "@/components/ui/empty"
import { Skeleton } from "@/components/ui/skeleton"

export type FileTreeProps = {
  tree: TreeNode | null // null while loading
  selected: string | null // the open note
  filter?: string // show only notes whose name matches (and their folders)
  onOpen(path: string): void
  onCreate(parentFolder: string, kind: "file" | "folder"): void
  onRename(path: string): void
  onMove(source: string, destinationFolder: string): void
  onDelete(path: string): void
  onScope?(folder: string): void
}

type Row = TreeNode & { id: string; children?: Row[] }

const ROW = 30
const OPEN_KEY = "aiblue-open-folders" // which folders were open, kept across reloads

function readOpen(): Record<string, boolean> {
  try {
    return JSON.parse(safeStorage.getItem(OPEN_KEY) ?? "{}")
  } catch {
    return {}
  }
}
const INDENT = 16

const withIds = (node: TreeNode): Row => ({
  ...node,
  id: node.path,
  children: node.children?.map(withIds),
})

/** react-arborist needs pixel sizes: the size of the element given to the returned callback ref
(a callback ref, because the element only mounts once the tree has loaded). */
function useSize() {
  const [el, setEl] = useState<HTMLDivElement | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    if (!el) return
    // a collapsed panel measures 0: keep the last real size, so the tree (and which folders are
    // open) survives hiding the panel
    const watch = new ResizeObserver(([e]) => {
      const { width, height } = e.contentRect
      if (width > 0 && height > 0) setSize({ width, height })
    })
    watch.observe(el)
    return () => watch.disconnect()
  }, [el])
  return [setEl, size] as const
}

type RowProps = NodeRendererProps<Row> &
  Pick<
    FileTreeProps,
    "selected" | "onOpen" | "onCreate" | "onRename" | "onDelete" | "onScope"
  >

function FileRow({
  node,
  style,
  dragHandle,
  selected,
  onOpen,
  onCreate,
  onRename,
  onDelete,
  onScope,
}: RowProps) {
  const folder = node.data.type === "folder"
  const name = folder ? node.data.name : node.data.name.replace(/\.md$/, "")
  const active = node.data.path === selected
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          ref={dragHandle}
          style={style}
          data-active={active || undefined}
          data-drop={node.willReceiveDrop || undefined}
          className={cn(
            "group/row relative mx-2 flex h-full cursor-default items-center gap-1.5 rounded-md pe-2 text-sm text-sidebar-foreground select-none",
            "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
            "data-[active]:bg-sidebar-accent data-[active]:font-medium data-[active]:text-sidebar-accent-foreground",
            "data-[drop]:ring-1 data-[drop]:ring-ring",
            "[&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-muted-foreground"
          )}
          onClick={() => (folder ? node.toggle() : onOpen(node.data.path))}
        >
          {/* one guide per level, so nesting reads at a glance */}
          {Array.from({ length: node.level }, (_, i) => (
            <span
              key={i}
              aria-hidden
              className="tree-guide absolute inset-y-0 w-px bg-sidebar-border"
              data-level={i}
            />
          ))}
          {folder ? (
            <ChevronRightIcon
              className={cn(
                "transition-transform duration-150",
                node.isOpen && "rotate-90"
              )}
            />
          ) : (
            <FileTextIcon />
          )}
          <span className="min-w-0 flex-1 truncate">{name}</span>
          {folder && node.children && (
            <span className="text-xs text-muted-foreground opacity-0 group-hover/row:opacity-100">
              {node.children.length}
            </span>
          )}
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-52">
        {folder && (
          <>
            <ContextMenuGroup>
              <ContextMenuItem
                onSelect={() => onCreate(node.data.path, "file")}
              >
                <FilePlusIcon />
                New note
              </ContextMenuItem>
              <ContextMenuItem
                onSelect={() => onCreate(node.data.path, "folder")}
              >
                <FolderPlusIcon />
                New folder
              </ContextMenuItem>
              {onScope && (
                <ContextMenuItem onSelect={() => onScope(node.data.path)}>
                  <MessageSquareTextIcon />
                  Ask about this folder
                </ContextMenuItem>
              )}
            </ContextMenuGroup>
            <ContextMenuSeparator />
          </>
        )}
        <ContextMenuGroup>
          <ContextMenuItem onSelect={() => onRename(node.data.path)}>
            <PencilIcon />
            Rename
          </ContextMenuItem>
          <ContextMenuItem
            variant="destructive"
            onSelect={() => onDelete(node.data.path)}
          >
            <Trash2Icon />
            Delete
          </ContextMenuItem>
        </ContextMenuGroup>
      </ContextMenuContent>
    </ContextMenu>
  )
}

export function FileTree({
  tree,
  selected,
  filter = "",
  onOpen,
  onCreate,
  onRename,
  onMove,
  onDelete,
  onScope,
}: FileTreeProps) {
  const [box, { width, height }] = useSize()
  const api = useRef<TreeApi<Row> | undefined>(undefined)
  const data = useMemo(
    () => (tree ? (withIds(tree).children ?? []) : []),
    [tree]
  )

  // keep the folders on the open note's path open (an agent's new note, a [[link]] followed)
  useEffect(() => {
    if (selected && api.current?.get(selected))
      api.current.openParents(selected)
  }, [selected, data])

  if (!tree)
    return (
      <div className="flex flex-col gap-2 px-4 py-2">
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-2/5" />
      </div>
    )

  if (data.length === 0)
    return (
      <Empty className="my-6">
        <EmptyHeader>
          <EmptyTitle>No notes yet</EmptyTitle>
          <EmptyDescription>Write one, or ask the agent to.</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <Button size="sm" onClick={() => onCreate("", "file")}>
            <FilePlusIcon data-icon="inline-start" />
            New note
          </Button>
        </EmptyContent>
      </Empty>
    )

  return (
    <div ref={box} className="min-h-0 flex-1">
      {width > 0 && height > 0 && (
        <Tree<Row>
          ref={api}
          data={data}
          width={width}
          height={height}
          rowHeight={ROW}
          indent={INDENT}
          paddingTop={4}
          paddingBottom={12}
          openByDefault={false}
          initialOpenState={readOpen()}
          onToggle={(id) => {
            const open = readOpen()
            open[id] = Boolean(api.current?.get(id)?.isOpen)
            safeStorage.setItem(OPEN_KEY, JSON.stringify(open))
          }}
          disableEdit
          disableMultiSelection
          searchTerm={filter}
          searchMatch={(n, term) =>
            n.data.type === "file" &&
            n.data.name.toLowerCase().includes(term.toLowerCase())
          }
          // the server sorts, so only the new parent matters, not the drop position
          onMove={({ dragIds, parentId }) =>
            dragIds.forEach((id) => onMove(id, parentId ?? ""))
          }
        >
          {(props) => (
            <FileRow
              {...props}
              selected={selected}
              onOpen={onOpen}
              onCreate={onCreate}
              onRename={onRename}
              onDelete={onDelete}
              onScope={onScope}
            />
          )}
        </Tree>
      )}
    </div>
  )
}
