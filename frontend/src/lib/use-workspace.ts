// The notes workspace's page state: the tree, the open note (what the server has vs what is typed),
// the graph. The server owns the files; every change goes through it and the tree is reloaded after.
import { useCallback, useEffect, useRef, useState } from "react"

import type { FileChange } from "@/lib/api"
import {
  ConflictError,
  nameOf,
  parentOf,
  workspace,
  type GraphData,
  type NoteFile,
  type TreeNode,
} from "@/lib/workspace"

const AUTOSAVE_MS = 800

type Open = { path: string; saved: string; sha: string; version: number }

export type Backlink = { path: string; line: number; text: string }

/** Every file path in the tree, depth first. */
function files(node: TreeNode | null): string[] {
  if (!node) return []
  if (node.type === "file") return [node.path]
  return (node.children ?? []).flatMap(files)
}

const stem = (path: string) =>
  nameOf(path)
    .toLowerCase()
    .replace(/\.(md|txt)$/, "")

/** "a" moved to "b": what a path under "a" is now called (null when it was not under "a"). */
function moved(path: string, from: string, to: string) {
  if (path === from) return to
  if (path.startsWith(from + "/")) return to + path.slice(from.length)
  return null
}

export function useWorkspace(onError: (message: string) => void) {
  const [tree, setTree] = useState<TreeNode | null>(null)
  const [open, setOpen] = useState<Open | null>(null)
  const [draft, setDraft] = useState("")
  const [conflict, setConflict] = useState<NoteFile | null>(null)
  const [backlinks, setBacklinks] = useState<Backlink[]>([])
  const [graph, setGraph] = useState<GraphData | null>(null)
  const [building, setBuilding] = useState(false)
  const [progress, setProgress] = useState<{
    done: number
    total: number
    tokens: number
  } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const version = useRef(0)
  const latest = useRef({ open, draft })
  useEffect(() => {
    latest.current = { open, draft }
  }, [open, draft])
  const fail = useCallback(
    (e: unknown) => onError(e instanceof Error ? e.message : String(e)),
    [onError]
  )

  const refreshTree = useCallback(
    () => workspace.tree().then(setTree, fail),
    [fail]
  )
  useEffect(() => {
    refreshTree()
  }, [refreshTree])

  const loadBacklinks = useCallback((path: string) => {
    workspace.backlinks(path).then(
      (r) => setBacklinks(r.links),
      () => setBacklinks([])
    )
  }, [])

  const show = useCallback(
    (file: NoteFile) => {
      version.current += 1
      setOpen({
        path: file.path,
        saved: file.content,
        sha: file.sha,
        version: version.current,
      })
      setDraft(file.content)
      setConflict(null)
      loadBacklinks(file.path)
    },
    [loadBacklinks]
  )

  const save = useCallback(
    async (force = false) => {
      if (timer.current) clearTimeout(timer.current)
      const { open, draft } = latest.current
      if (!open || draft === open.saved) return
      try {
        const file = await workspace.save(
          open.path,
          draft,
          force ? undefined : open.sha
        )
        // keep the editor's text (the user may have typed on); only the server's version moves
        setOpen((o) =>
          o && o.path === file.path
            ? { ...o, saved: file.content, sha: file.sha }
            : o
        )
        setConflict(null)
      } catch (e) {
        if (e instanceof ConflictError) setConflict(e.current)
        else fail(e)
      }
    },
    [fail]
  )

  const edit = useCallback(
    (text: string) => {
      setDraft(text)
      if (timer.current) clearTimeout(timer.current)
      timer.current = setTimeout(() => save(), AUTOSAVE_MS)
    },
    [save]
  )

  const openNote = useCallback(
    async (path: string) => {
      await save()
      try {
        show(await workspace.read(path))
      } catch (e) {
        fail(e)
      }
    },
    [save, show, fail]
  )

  /** The conflict banner: keep what is typed (overwrite the server) or take the server's version. */
  const resolve = useCallback(
    async (keep: "mine" | "theirs") => {
      if (!conflict) return
      if (keep === "theirs") show(conflict)
      else await save(true)
    },
    [conflict, show, save]
  )

  const close = useCallback(() => {
    setOpen(null)
    setDraft("")
    setConflict(null)
    setBacklinks([])
  }, [])

  const create = useCallback(
    async (parent: string, name: string, kind: "file" | "folder") => {
      const path = parent ? `${parent}/${name}` : name
      try {
        const made = await workspace.create(path, kind)
        await refreshTree()
        if (kind === "file") await openNote(made.path)
      } catch (e) {
        fail(e)
      }
    },
    [refreshTree, openNote, fail]
  )

  const follow = useCallback((from: string, to: string) => {
    setOpen((o) => {
      const now = o && moved(o.path, from, to)
      return o && now ? { ...o, path: now } : o
    })
  }, [])

  const move = useCallback(
    async (source: string, destination: string) => {
      await save()
      try {
        const r = await workspace.move(source, destination)
        follow(source, r.path)
        await refreshTree()
      } catch (e) {
        fail(e)
      }
    },
    [save, follow, refreshTree, fail]
  )

  const rename = useCallback(
    async (path: string, name: string) => {
      const parent = parentOf(path)
      const keepExt = /\.(md|txt)$/.test(path) && !/\.\w+$/.test(name)
      const next =
        (parent ? `${parent}/` : "") +
        name +
        (keepExt ? path.slice(path.lastIndexOf(".")) : "")
      if (next !== path) await move(path, next)
    },
    [move]
  )

  const remove = useCallback(
    async (path: string) => {
      try {
        await workspace.remove(path)
        const o = latest.current.open
        if (o && moved(o.path, path, "") !== null) close()
        await refreshTree()
      } catch (e) {
        fail(e)
      }
    },
    [close, refreshTree, fail]
  )

  /** [[name]] in the preview: the note with that name (nearest folder first), or a new one at the root. */
  const openLink = useCallback(
    async (name: string) => {
      const want = stem(name)
      const all = files(tree)
      const here = parentOf(latest.current.open?.path ?? "")
      const hits = all.filter((p) =>
        name.includes("/")
          ? p
              .toLowerCase()
              .replace(/\.(md|txt)$/, "")
              .endsWith(name.toLowerCase())
          : stem(p) === want
      )
      const best = hits.find((p) => parentOf(p) === here) ?? hits[0]
      if (best) await openNote(best)
      else await create("", name, "file")
    },
    [tree, openNote, create]
  )

  // ---- what the agent did -------------------------------------------------------------------------
  const reloadTree = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onFileChanged = useCallback(
    (change: FileChange) => {
      if (reloadTree.current) clearTimeout(reloadTree.current)
      reloadTree.current = setTimeout(refreshTree, 150) // a burst of changes reloads once
      const { open, draft } = latest.current
      if (!open) return
      if (change.kind === "move" && change.dest)
        return follow(change.path, change.dest)
      if (
        change.kind === "delete" &&
        moved(open.path, change.path, "") !== null
      )
        return close()
      if (change.kind === "write" && change.path === open.path)
        workspace.read(open.path).then((file) => {
          if (file.sha === open.sha) return
          if (draft === open.saved)
            show(file) // nothing typed: take the agent's version
          else setConflict(file) // both changed it: the person decides
        }, fail)
      else if (change.kind === "write") loadBacklinks(open.path)
    },
    [refreshTree, follow, close, show, loadBacklinks, fail]
  )

  // ---- graph --------------------------------------------------------------------------------------
  const loadGraph = useCallback(
    (folder = "") => workspace.graph(folder).then(setGraph, fail),
    [fail]
  )

  const buildGraph = useCallback(
    async (folder = "") => {
      setBuilding(true)
      setProgress(null)
      try {
        for await (const ev of workspace.buildGraph(folder)) {
          if (ev.type === "error") fail(ev.message)
          else {
            setProgress({ done: ev.done, total: ev.total, tokens: ev.tokens })
            if (ev.type === "done" && ev.stopped)
              onError(`Stopped after ${ev.done} of ${ev.total}: ${ev.stopped}`)
          }
        }
      } catch (e) {
        fail(e)
      } finally {
        setBuilding(false)
        loadGraph(folder)
      }
    },
    [fail, onError, loadGraph]
  )

  return {
    tree,
    refreshTree,
    open,
    draft,
    dirty: open !== null && draft !== open.saved,
    conflict,
    resolve,
    backlinks,
    openNote,
    openLink,
    edit,
    save,
    close,
    create,
    rename,
    move,
    remove,
    onFileChanged,
    graph,
    loadGraph,
    buildGraph,
    building,
    progress,
  }
}
