// The note editor: CodeMirror 6 over the markdown text itself, so what is on screen is the file -
// an agent's edit lands exactly where it says. Plus the read view (react-markdown) with [[links]].
import { useEffect, useRef, type ComponentProps } from "react"
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from "@codemirror/commands"
import { markdown } from "@codemirror/lang-markdown"
import {
  HighlightStyle,
  syntaxHighlighting,
  syntaxTree,
} from "@codemirror/language"
import { languages } from "@codemirror/language-data"
import { Compartment, EditorState, RangeSetBuilder } from "@codemirror/state"
import {
  Decoration,
  drawSelection,
  EditorView,
  keymap,
  placeholder,
  ViewPlugin,
  type DecorationSet,
  type ViewUpdate,
} from "@codemirror/view"
import { tags } from "@lezer/highlight"
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"
import { ScrollArea } from "@/components/ui/scroll-area"

/** The editor's look, from the app's theme variables only (so light and dark both follow the page). */
export const noteTheme = EditorView.theme({
  "&": {
    height: "100%",
    backgroundColor: "var(--background)",
    color: "var(--foreground)",
  },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": {
    fontFamily: "var(--font-sans)",
    fontSize: "15px",
    lineHeight: "1.7", // Thai vowels and tone marks stack above and below the line
  },
  ".cm-content": {
    padding: "40px 48px 80px",
    maxWidth: "48rem",
    margin: "0 auto",
    caretColor: "var(--foreground)",
  },
  ".cm-cursor": { borderLeftColor: "var(--foreground)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": {
    backgroundColor: "color-mix(in oklch, var(--primary) 22%, transparent)",
  },
  ".cm-placeholder": { color: "var(--muted-foreground)" },
  ".cm-wikilink": {
    color: "var(--graph-note)",
    textDecoration: "underline",
    textDecorationColor:
      "color-mix(in oklch, var(--graph-note) 40%, transparent)",
    textUnderlineOffset: "3px",
  },
  ".cm-gutters": {
    backgroundColor: "var(--background)",
    color: "var(--muted-foreground)",
    border: "none",
  },
})

export const noteHighlight = syntaxHighlighting(
  HighlightStyle.define([
    { tag: tags.heading1, fontSize: "1.6em", fontWeight: "600" },
    { tag: tags.heading2, fontSize: "1.3em", fontWeight: "600" },
    { tag: tags.heading3, fontSize: "1.15em", fontWeight: "600" },
    { tag: [tags.heading4, tags.heading5, tags.heading6], fontWeight: "600" },
    { tag: tags.strong, fontWeight: "600" },
    { tag: tags.emphasis, fontStyle: "italic" },
    { tag: tags.strikethrough, textDecoration: "line-through" },
    { tag: [tags.link, tags.url], color: "var(--primary)" },
    { tag: tags.monospace, fontFamily: "var(--font-mono, monospace)" },
    { tag: tags.quote, color: "var(--muted-foreground)" },
    {
      tag: [tags.processingInstruction, tags.meta],
      color: "var(--muted-foreground)",
    },
  ])
)

export const noteLanguage = markdown({ codeLanguages: languages })

// Live preview, the way Obsidian edits: the markup that only says "this is a heading / bold / a link"
// (#, **, `, the brackets of [[links]]) is hidden on every line except the ones the cursor is on, so a
// note reads as text while the file stays plain markdown underneath.
const HIDE = new Set([
  "HeaderMark",
  "EmphasisMark",
  "CodeMark",
  "StrikethroughMark",
])
const hidden = Decoration.replace({})
const wikilink = Decoration.mark({ class: "cm-wikilink" })
const WIKI = /\[\[([^\]\n]+?)\]\]/g

function livePreview(view: EditorView): DecorationSet {
  const { state } = view
  const active = new Set<number>()
  // markup shows only where the person is typing; an unfocused editor shows none
  for (const r of view.hasFocus ? state.selection.ranges : []) {
    const from = state.doc.lineAt(r.from).number
    const to = state.doc.lineAt(r.to).number
    for (let n = from; n <= to; n++) active.add(n)
  }
  const ranges: [number, number, Decoration][] = []
  for (const { from, to } of view.visibleRanges) {
    syntaxTree(state).iterate({
      from,
      to,
      enter: (node) => {
        if (!HIDE.has(node.name)) return
        if (active.has(state.doc.lineAt(node.from).number)) return
        // "## Title": hide the marks and the space after them
        let end = node.to
        if (
          node.name === "HeaderMark" &&
          state.doc.sliceString(end, end + 1) === " "
        )
          end += 1
        ranges.push([node.from, end, hidden])
      },
    })
    const text = state.doc.sliceString(from, to)
    for (const m of text.matchAll(WIKI)) {
      const start = from + (m.index ?? 0)
      const end = start + m[0].length
      const line = state.doc.lineAt(start).number
      if (active.has(line)) {
        ranges.push([start, end, wikilink])
      } else {
        ranges.push([start, start + 2, hidden])
        ranges.push([start + 2, end - 2, wikilink])
        ranges.push([end - 2, end, hidden])
      }
    }
  }
  ranges.sort((a, b) => a[0] - b[0] || a[1] - b[1])
  const builder = new RangeSetBuilder<Decoration>()
  let last = -1
  for (const [f, t, d] of ranges) {
    if (f < last) continue // overlapping marks (a link inside bold): keep the first
    builder.add(f, t, d)
    last = t
  }
  return builder.finish()
}

export const notePreview = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet
    constructor(view: EditorView) {
      this.decorations = livePreview(view)
    }
    update(u: ViewUpdate) {
      if (u.docChanged || u.selectionSet || u.viewportChanged || u.focusChanged)
        this.decorations = livePreview(u.view)
    }
  },
  { decorations: (v) => v.decorations }
)

export type NoteEditorProps = {
  value: string
  /** Bumps when the app loads text from outside (another note, the agent's edit): the document is replaced. */
  version: number
  onChange(text: string): void
  onSave(): void
  readOnly?: boolean
}

export function NoteEditor({
  value,
  version,
  onChange,
  onSave,
  readOnly = false,
}: NoteEditorProps) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const editable = useRef(new Compartment())
  const callbacks = useRef({ onChange, onSave })
  const initial = useRef({ value, readOnly })
  useEffect(() => {
    callbacks.current = { onChange, onSave }
  }, [onChange, onSave])

  useEffect(() => {
    if (!host.current) return
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({
        doc: initial.current.value,
        extensions: [
          history(),
          drawSelection(),
          keymap.of([
            {
              key: "Mod-s",
              preventDefault: true,
              run: () => (callbacks.current.onSave(), true),
            },
            indentWithTab,
            ...defaultKeymap,
            ...historyKeymap,
          ]),
          noteLanguage,
          noteHighlight,
          noteTheme,
          notePreview,
          EditorView.lineWrapping,
          placeholder(
            "Start writing. # A title, ## sections, [[another note]] to link."
          ),
          editable.current.of([
            EditorState.readOnly.of(initial.current.readOnly),
            EditorView.editable.of(!initial.current.readOnly),
          ]),
          EditorView.updateListener.of((u) => {
            // a replacement from outside is not the person typing: it must not count as an edit
            if (
              u.docChanged &&
              !u.transactions.some((t) => t.isUserEvent("external"))
            )
              callbacks.current.onChange(u.state.doc.toString())
          }),
        ],
      }),
    })
    view.current = v
    return () => {
      v.destroy()
      view.current = null
    }
  }, [])

  // a new version from outside: replace the text (the value prop alone changes on every keystroke)
  useEffect(() => {
    const v = view.current
    if (!v) return
    const now = v.state.doc.toString()
    if (now !== value)
      v.dispatch({
        changes: { from: 0, to: now.length, insert: value },
        selection: { anchor: 0 },
        scrollIntoView: true,
        userEvent: "external",
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only a version bump replaces the document
  }, [version])

  useEffect(() => {
    view.current?.dispatch({
      effects: editable.current.reconfigure([
        EditorState.readOnly.of(readOnly),
        EditorView.editable.of(!readOnly),
      ]),
    })
  }, [readOnly])

  return <div ref={host} className="h-full min-h-0" />
}

// ---- read view ------------------------------------------------------------------------------------

const WIKILINK = /\[\[([^\]|\n]+?)(?:\|([^\]\n]+?))?\]\]/g

/** [[target]] / [[target|label]] -> markdown links on a wikilink: scheme the renderer turns into buttons. */
function withLinks(text: string) {
  return text.replace(WIKILINK, (_, target: string, label?: string) => {
    const shown = (label ?? target).trim().replace(/[[\]]/g, "\\$&")
    return `[${shown}](wikilink:${encodeURIComponent(target.trim())})`
  })
}

type MdProps<T extends keyof React.JSX.IntrinsicElements> =
  ComponentProps<T> & { node?: unknown }

export type MarkdownPreviewProps = {
  value: string
  onOpenLink(name: string): void
}

export function MarkdownPreview({ value, onOpenLink }: MarkdownPreviewProps) {
  return (
    <ScrollArea className="h-full">
      <article className="mx-auto flex max-w-3xl flex-col gap-3 px-8 py-6 leading-relaxed">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          urlTransform={(url) =>
            url.startsWith("wikilink:") ? url : defaultUrlTransform(url)
          }
          components={{
            h1: ({ node: _, ...p }: MdProps<"h1">) => (
              <h1 className="text-2xl font-semibold" {...p} />
            ),
            h2: ({ node: _, ...p }: MdProps<"h2">) => (
              <h2 className="mt-2 text-xl font-semibold" {...p} />
            ),
            h3: ({ node: _, ...p }: MdProps<"h3">) => (
              <h3 className="mt-1 text-lg font-semibold" {...p} />
            ),
            ul: ({ node: _, ...p }: MdProps<"ul">) => (
              <ul className="ms-5 flex list-disc flex-col gap-1" {...p} />
            ),
            ol: ({ node: _, ...p }: MdProps<"ol">) => (
              <ol className="ms-5 flex list-decimal flex-col gap-1" {...p} />
            ),
            blockquote: ({ node: _, ...p }: MdProps<"blockquote">) => (
              <blockquote
                className="border-s-2 ps-4 text-muted-foreground"
                {...p}
              />
            ),
            code: ({ node: _, className, ...p }: MdProps<"code">) => (
              <code
                className={cn(
                  "rounded-sm bg-muted px-1 font-mono text-sm",
                  className
                )}
                {...p}
              />
            ),
            pre: ({ node: _, ...p }: MdProps<"pre">) => (
              <pre
                className="overflow-x-auto rounded-md bg-muted p-3 text-sm [&_code]:bg-transparent [&_code]:p-0"
                {...p}
              />
            ),
            table: ({ node: _, ...p }: MdProps<"table">) => (
              <div className="overflow-x-auto">
                <table className="w-full text-sm" {...p} />
              </div>
            ),
            th: ({ node: _, ...p }: MdProps<"th">) => (
              <th
                className="border-b px-2 py-1 text-start font-medium"
                {...p}
              />
            ),
            td: ({ node: _, ...p }: MdProps<"td">) => (
              <td className="border-b px-2 py-1" {...p} />
            ),
            a: ({ node: _, href = "", children, ...p }: MdProps<"a">) =>
              href.startsWith("wikilink:") ? (
                <button
                  type="button"
                  className="text-primary underline underline-offset-2"
                  onClick={() =>
                    onOpenLink(
                      decodeURIComponent(href.slice("wikilink:".length))
                    )
                  }
                >
                  {children}
                </button>
              ) : (
                <a
                  className="text-primary underline underline-offset-2"
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  {...p}
                >
                  {children}
                </a>
              ),
          }}
        >
          {withLinks(value)}
        </ReactMarkdown>
      </article>
    </ScrollArea>
  )
}
