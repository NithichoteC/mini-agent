// The workspace file tree (react-arborist): folders first, drag to move, right-click for the rest.
import * as React from "react"
import { Tree, type NodeRendererProps, type TreeApi } from "react-arborist"
import {
  ChevronRightIcon,
  FolderIcon,
  FolderOpenIcon,
  FileTextIcon,
  PlusIcon,
  TrashIcon,
  PencilIcon,
} from "lucide-react"

import type { TreeNode } from "@/lib/workspace"
import { cn } from "@/lib/utils"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export type FileTreeProps = {
  tree: TreeNode | null // root node (path ""), null while loading -> show Skeleton rows
  selected: string | null // the open file's path, highlighted
  onOpen(path: string): void // a file was clicked (or Enter)
  onCreate(parentFolder: string, kind: "file" | "folder"): void // caller asks for the name
  onRename(path: string): void // caller asks for the new name
  onMove(source: string, destinationFolder: string): void // drag and drop; "" = root
  onDelete(path: string): void // caller confirms
  onScope?(folder: string): void // "Ask the agent about this folder"
}

// Hook to measure parent div with ResizeObserver
function useMeasure(): [
  React.RefObject<HTMLDivElement | null>,
  { width: number; height: number },
] {
  const ref = React.useRef<HTMLDivElement | null>(null)
  const [size, setSize] = React.useState({ width: 0, height: 0 })

  React.useEffect(() => {
    if (!ref.current) return

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) {
        setSize({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        })
      }
    })

    observer.observe(ref.current)
    return () => observer.disconnect()
  }, [])

  return [ref, size]
}

type FileNodeWithId = TreeNode & { id: string }

function convertToFileNodes(node: TreeNode): FileNodeWithId {
  const fileNode: FileNodeWithId = {
    ...node,
    id: node.path || "root",
  }
  if (node.children) {
    fileNode.children = node.children.map((child) => convertToFileNodes(child))
  }
  return fileNode
}

interface FileRowProps extends NodeRendererProps<FileNodeWithId> {
  selected: string | null
  onOpen: (path: string) => void
  onCreate: (parentFolder: string, kind: "file" | "folder") => void
  onRename: (path: string) => void
  onDelete: (path: string) => void
  onScope?: (folder: string) => void
}

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
}: FileRowProps) {
  const isFolder = node.data.type === "folder"
  const isSelected = node.data.path === selected

  // Show the name without ".md" suffix for files
  const displayName =
    !isFolder && node.data.name.endsWith(".md")
      ? node.data.name.slice(0, -3)
      : node.data.name

  const handleContextMenuCreate = (kind: "file" | "folder") => {
    onCreate(node.data.path, kind)
  }

  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>
        <div
          ref={dragHandle}
          style={style}
          className={cn(
            "flex cursor-pointer items-center gap-1.5 rounded-md px-1.5 text-sm outline-hidden transition-colors [&_svg]:size-4 [&_svg]:shrink-0 [&_svg]:text-muted-foreground",
            "hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
            isSelected &&
              "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
          )}
          data-selected={isSelected ? "true" : undefined}
          onClick={(e) => {
            if (isFolder) {
              node.toggle()
            } else {
              onOpen(node.data.path)
            }
            e.stopPropagation()
          }}
          onDoubleClick={(e) => {
            if (!isFolder) {
              onOpen(node.data.path)
            }
            e.stopPropagation()
          }}
        >
          {/* Chevron for folders */}
          {isFolder && (
            <span
              className={cn(
                "inline-flex shrink-0 transition-transform duration-200",
                node.isOpen && "rotate-90"
              )}
            >
              <ChevronRightIcon />
            </span>
          )}

          {/* Folder/File Icon */}
          {isFolder ? (
            node.isOpen ? (
              <FolderOpenIcon />
            ) : (
              <FolderIcon />
            )
          ) : (
            <FileTextIcon />
          )}

          {/* Name label */}
          <span className="min-w-0 flex-1 truncate text-sm/tight">
            {displayName}
          </span>
        </div>
      </ContextMenuTrigger>

      <ContextMenuContent className="w-48">
        {isFolder && (
          <>
            <ContextMenuItem onClick={() => handleContextMenuCreate("file")}>
              <FileTextIcon />
              New note
            </ContextMenuItem>
            <ContextMenuItem onClick={() => handleContextMenuCreate("folder")}>
              <FolderIcon />
              New folder
            </ContextMenuItem>
            <ContextMenuSeparator />
          </>
        )}

        <ContextMenuItem onClick={() => onRename(node.data.path)}>
          <PencilIcon />
          Rename
        </ContextMenuItem>

        {isFolder && onScope && (
          <>
            <ContextMenuSeparator />
            <ContextMenuItem onClick={() => onScope(node.data.path)}>
              <PlusIcon />
              Ask about this folder
            </ContextMenuItem>
          </>
        )}

        <ContextMenuSeparator />

        <ContextMenuItem
          variant="destructive"
          onClick={() => onDelete(node.data.path)}
        >
          <TrashIcon />
          Delete
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
}

export function FileTree({
  tree,
  selected,
  onOpen,
  onCreate,
  onRename,
  onMove,
  onDelete,
  onScope,
}: FileTreeProps) {
  const [containerRef, { width, height }] = useMeasure()
  const treeRef = React.useRef<TreeApi<FileNodeWithId> | undefined>(undefined)

  // Convert tree to format for react-arborist
  const data = React.useMemo(() => {
    if (!tree) return []
    const root = convertToFileNodes(tree)
    // Return children of root (not the root itself)
    return (root.children as FileNodeWithId[]) || []
  }, [tree])

  // Keep folders on the selected path open (an agent's new note, a [[link]] followed)
  React.useEffect(() => {
    if (selected && treeRef.current?.get(selected))
      treeRef.current.openParents(selected)
  }, [selected, data])

  // Handle drag and drop
  // the server sorts, so only the new parent matters, not the drop index
  const handleMove = ({
    dragIds,
    parentId,
  }: {
    dragIds: string[]
    parentId: string | null
  }) => dragIds.forEach((id) => onMove(id, parentId ?? ""))

  const isLoading = tree === null

  return (
    <div ref={containerRef} className="flex h-full min-h-0 w-full flex-col">
      {isLoading ? (
        // Show skeleton rows while loading
        <div className="flex flex-col gap-1 p-2">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-8 w-full" />
          ))}
        </div>
      ) : data.length === 0 ? (
        // Empty state
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <FileTextIcon />
            </EmptyMedia>
            <EmptyTitle>No notes yet</EmptyTitle>
            <EmptyDescription>
              Get started by creating a new note.
            </EmptyDescription>
          </EmptyHeader>
          <EmptyContent>
            <Button onClick={() => onCreate("", "file")} size="sm">
              <PlusIcon data-icon="inline-start" />
              New note
            </Button>
          </EmptyContent>
        </Empty>
      ) : width > 0 && height > 0 ? (
        // react-arborist needs pixel sizes: render once the container is measured
        <Tree
          ref={treeRef}
          data={data}
          width={width}
          height={height}
          rowHeight={28}
          indent={14}
          padding={4}
          openByDefault={false}
          disableEdit
          disableMultiSelection
          onMove={handleMove}
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
      ) : null}
    </div>
  )
}
