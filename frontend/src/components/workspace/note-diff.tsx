// What a change would do to a note, read-only: CodeMirror's unified merge view of before -> after.
// Shown on an approval card (the agent's write / edit) and in the conflict banner.
import { useEffect, useRef } from "react"
import { unifiedMergeView } from "@codemirror/merge"
import { EditorState } from "@codemirror/state"
import { EditorView } from "@codemirror/view"

import {
  noteHighlight,
  noteLanguage,
  noteTheme,
} from "@/components/workspace/note-editor"

const diffTheme = EditorView.theme({
  "&": { maxHeight: "20rem", fontSize: "13px" },
  ".cm-scroller": { fontSize: "13px", lineHeight: "1.5" },
  ".cm-content": { padding: "8px 0", maxWidth: "none" },
  ".cm-changedLine, .cm-insertedLine": {
    backgroundColor: "color-mix(in oklch, var(--chart-2) 16%, transparent)",
  },
  ".cm-changedText": {
    background: "color-mix(in oklch, var(--chart-2) 35%, transparent)",
  },
  ".cm-deletedChunk": {
    backgroundColor: "color-mix(in oklch, var(--destructive) 12%, transparent)",
  },
  ".cm-deletedChunk .cm-deletedText, .cm-deletedChunk del": {
    background: "color-mix(in oklch, var(--destructive) 25%, transparent)",
  },
})

export type NoteDiffProps = { before: string; after: string; path?: string }

export function NoteDiff({ before, after, path }: NoteDiffProps) {
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!host.current) return
    const view = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: after,
        extensions: [
          noteLanguage,
          noteHighlight,
          noteTheme,
          diffTheme,
          EditorView.lineWrapping,
          EditorState.readOnly.of(true),
          EditorView.editable.of(false),
          unifiedMergeView({
            original: before,
            mergeControls: false,
            highlightChanges: true,
            gutter: true,
          }),
        ],
      }),
    })
    return () => view.destroy()
  }, [before, after])

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs text-muted-foreground">
        {before === ""
          ? `${path ?? "This file"} is new`
          : `Changes to ${path ?? "the note"}`}
      </span>
      <div ref={host} className="overflow-hidden rounded-md border" />
    </div>
  )
}
