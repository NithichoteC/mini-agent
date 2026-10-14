// The left panel: the workspace, a filter, the file tree, and the app's own controls at the bottom
// (theme, settings) - the place Obsidian keeps them, away from the note and the chat.
import { useMemo, useState } from "react"
import {
  FilePlusIcon,
  FolderPlusIcon,
  MoonIcon,
  SearchIcon,
  SparklesIcon,
  SunIcon,
  XIcon,
} from "lucide-react"

import type { TreeNode } from "@/lib/workspace"
import { useTheme } from "@/components/theme-provider"
import { FileTree, type FileTreeProps } from "@/components/workspace/file-tree"
import { Button } from "@/components/ui/button"
import {
  InputGroup,
  InputGroupAddon,
  InputGroupButton,
  InputGroupInput,
} from "@/components/ui/input-group"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"

function count(node: TreeNode | null): number {
  if (!node) return 0
  if (node.type === "file") return 1
  return (node.children ?? []).reduce((n, c) => n + count(c), 0)
}

function IconAction({
  label,
  onClick,
  children,
  disabled,
}: {
  label: string
  onClick?: () => void
  children: React.ReactNode
  disabled?: boolean
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={label}
          onClick={onClick}
          disabled={disabled}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

export function ThemeToggle() {
  const { theme, setTheme } = useTheme()
  const dark =
    theme === "dark" ||
    (theme === "system" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches)
  return (
    <IconAction
      label={dark ? "Light theme" : "Dark theme"}
      onClick={() => setTheme(dark ? "light" : "dark")}
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </IconAction>
  )
}

export type FilesPanelProps = Omit<FileTreeProps, "filter">

export function FilesPanel(tree: FilesPanelProps) {
  const [filter, setFilter] = useState("")
  const notes = useMemo(() => count(tree.tree), [tree.tree])
  return (
    <div className="flex h-full min-h-0 flex-col bg-sidebar text-sidebar-foreground">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-sidebar-border px-3">
        <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <SparklesIcon className="size-4" />
        </div>
        <div className="flex min-w-0 flex-1 flex-col leading-tight">
          <span className="truncate text-sm font-semibold">AI-Blue</span>
          <span className="truncate text-xs text-muted-foreground">
            {notes} note{notes === 1 ? "" : "s"}
          </span>
        </div>
        <IconAction label="New note" onClick={() => tree.onCreate("", "file")}>
          <FilePlusIcon />
        </IconAction>
        <IconAction
          label="New folder"
          onClick={() => tree.onCreate("", "folder")}
        >
          <FolderPlusIcon />
        </IconAction>
      </div>

      <div className="shrink-0 px-3 pt-3 pb-1">
        <InputGroup>
          <InputGroupAddon>
            <SearchIcon />
          </InputGroupAddon>
          <InputGroupInput
            placeholder="Filter notes"
            aria-label="Filter notes by name"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          {filter && (
            <InputGroupAddon align="inline-end">
              <InputGroupButton
                size="icon-xs"
                aria-label="Clear the filter"
                onClick={() => setFilter("")}
              >
                <XIcon />
              </InputGroupButton>
            </InputGroupAddon>
          )}
        </InputGroup>
      </div>

      <FileTree {...tree} filter={filter} />
    </div>
  )
}
