// The notes workspace: backend/app.py's /api/workspace/* contract. Paths are workspace-relative, "/"-separated.

export type TreeNode = {
  name: string
  path: string // "" for the root
  type: "folder" | "file"
  size?: number
  children?: TreeNode[]
}

export type NoteFile = { path: string; content: string; sha: string }

export type SearchHit = {
  path: string
  heading: string
  line: number
  text: string
  score: number
}

export type GraphNode = {
  id: string // "note:<path>" or "entity:<name>"
  label: string
  kind: "note" | "entity"
  path?: string // notes only
  degree: number
}

export type GraphEdge = {
  source: string
  target: string
  kind: "link" | "relation" | "mention" // note->note [[link]], entity->entity triple, note->entity
  label?: string // the relation, for kind "relation"
  evidence?: string
  path?: string // the note the relation was read from
}

export type GraphStats = {
  notes: number
  entities: number
  relations: number
  links: number
  chunks: number
  extracted: number // chunks already read by the extractor
  pending: number // chunks not read yet (new or edited since)
}

export type GraphData = {
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: GraphStats
}

export type BuildEvent =
  | {
      type: "progress"
      done: number
      total: number
      tokens: number
      triples: number
    }
  | {
      type: "done"
      done: number
      total: number
      tokens: number
      triples: number
      failed: number
      stopped?: string
    }
  | { type: "error"; message: string }

export class ConflictError extends Error {
  current: NoteFile
  constructor(message: string, current: NoteFile) {
    super(message)
    this.current = current
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    const detail = body?.detail
    if (res.status === 409 && detail?.current)
      throw new ConflictError(detail.message, detail.current)
    throw new Error(
      typeof detail === "string" ? detail : detail?.message || res.statusText
    )
  }
  return res.json() as Promise<T>
}

const post = <T>(url: string, body: unknown) =>
  call<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })

const q = (params: Record<string, string | number | undefined>) =>
  new URLSearchParams(
    Object.entries(params)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)])
  ).toString()

export const workspace = {
  tree: () => call<TreeNode>("/api/workspace/tree"),
  read: (path: string) => call<NoteFile>(`/api/workspace/file?${q({ path })}`),
  /** baseSha = the sha the editor loaded; a 409 ConflictError means the file changed meanwhile. */
  save: (path: string, content: string, baseSha?: string) =>
    call<NoteFile>("/api/workspace/file", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, content, base_sha: baseSha }),
    }),
  create: (path: string, kind: "file" | "folder") =>
    post<{ path: string }>("/api/workspace/create", { path, kind }),
  move: (source: string, destination: string) =>
    post<{ path: string }>("/api/workspace/move", { source, destination }),
  remove: (path: string) =>
    post<{ ok: true }>("/api/workspace/delete", { path }),
  search: (query: string, folder = "", k = 8) =>
    call<SearchHit[]>(`/api/workspace/search?${q({ q: query, folder, k })}`),
  backlinks: (path: string) =>
    call<{
      path: string
      links: { path: string; line: number; text: string }[]
    }>(`/api/workspace/backlinks?${q({ path })}`),
  graph: (folder = "") =>
    call<GraphData>(`/api/workspace/graph?${q({ folder })}`),
  /** Reads not-yet-extracted chunks into triples; yields progress events until done or error. */
  async *buildGraph(
    folder = "",
    signal?: AbortSignal
  ): AsyncGenerator<BuildEvent> {
    const res = await fetch("/api/workspace/graph/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ folder }),
      signal,
    })
    if (!res.ok || !res.body) {
      const body = await res.json().catch(() => ({}))
      yield { type: "error", message: body?.detail || res.statusText }
      return
    }
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
    let buf = ""
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return
      buf += value
      const lines = buf.split("\n")
      buf = lines.pop() ?? ""
      for (const line of lines)
        if (line.startsWith("data: "))
          yield JSON.parse(line.slice(6)) as BuildEvent
    }
  },
}

/** "a/b/c.md" -> "a/b"; "c.md" -> "" */
export const parentOf = (path: string) =>
  path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : ""

export const nameOf = (path: string) => path.slice(path.lastIndexOf("/") + 1)
