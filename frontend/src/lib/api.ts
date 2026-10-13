// The server's contract (server.py). Everything the page knows comes from these calls.

export type ToolState = {
  status: "completed" | "denied" | "error" | "pending"
  decision: string | null
  input: Record<string, unknown> | string | null
  output: string | null
  duration_ms: number | null
}

export type Part =
  | { type: "text"; text: string }
  | { type: "reasoning"; text: string }
  | { type: "review"; text: string; model: string | null; step: string }
  | { type: "error"; text: string; model: string | null; step: string }
  | {
      type: "tool"
      tool: string
      title: string
      run: number | null
      state: ToolState
      approval?: Approval
    }

export type Approval = { id: string; always: string; answered?: boolean }

export type Attached = { name: string; note: string }

export type Turn = {
  id: string
  role: "user" | "assistant"
  kind?: "message" | "hint"
  text?: string
  attachments?: Attached[]
  parts?: Part[]
  status?: string // assistant turn: running, done, blocked, max_runs, no_progress, llm_error, error
}

export type SessionRow = {
  id: string
  title: string
  status: string | null
  started: string
  updated: string
  tokens: number
  running: boolean
}

export type Transcript = {
  info: {
    id: string
    title: string
    status: string | null
    tokens: number
    running: boolean
    model: string
  }
  messages: {
    info: {
      role: "user" | "assistant"
      kind?: "message" | "hint"
      time: string
    }
    parts: Part[]
    text?: string
    attachments?: Attached[]
  }[]
}

export type TraceStep = {
  id: number
  run: number
  step_id: string
  model: string | null
  tool: string | null
  title: string | null
  args: Record<string, unknown> | string | null
  decision: string
  ok: number
  model_output: string
  observation: string
  duration_ms: number
  tokens: number
  created_at: string
}

export type Settings = {
  actor: string
  reviewer: string
  actions: string
  review: string
  tools: string[]
  permissions: { tool: string; pattern: string; action: string }[]
  rag: { type: string; k: number; full_text_tokens: number; ocr: boolean }
  loop: { max_runs: number; max_repeats: number }
}

export type SettingsDoc = {
  values: Settings
  files: string[]
  options: {
    models: { key: string; model: string; vendor: string }[]
    actions: string[]
    review: string[]
    rag_types: string[]
    permission_actions: string[]
    tools: { name: string; description: string; permission: string }[]
  }
}

export type Meter = {
  model: string
  used: number
  cap?: number | null
  window?: number | null
  context_window?: number | null
  request_tokens?: number | null
  percent: number | null
}
export type Meters = { context: Meter | null; daily: Meter[]; note: string }

export type ServerEvent =
  | { type: "turn"; turn: string }
  | { type: "session.start"; session: string; task: string; model: string }
  | { type: "step"; session: string; step_id: string; parts: Part[] }
  | {
      type: "approval"
      id: string
      tool: string
      args: Record<string, unknown>
      title: string
      always: string
    }
  | {
      type: "session.end"
      status: string
      answer: string
      trace_id: string
      total_tokens: number
      error?: { message: string }
    }
  | { type: "error"; message: string }

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body.detail)
        detail =
          typeof body.detail === "string"
            ? body.detail
            : JSON.stringify(body.detail)
    } catch {
      // not json: keep the status line
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
})

export const api = {
  sessions: () => call<SessionRow[]>("/api/sessions"),
  session: (id: string) =>
    call<Transcript>(`/api/sessions/${encodeURIComponent(id)}`),
  trace: (id: string) =>
    call<{ session: Record<string, unknown>; steps: TraceStep[] }>(
      `/api/sessions/${encodeURIComponent(id)}/trace`
    ),
  transcriptUrl: (id: string) =>
    `/api/sessions/${encodeURIComponent(id)}/transcript`,
  settings: () => call<SettingsDoc>("/api/settings"),
  saveSettings: (s: Partial<Settings>) =>
    call<Settings>("/api/settings", json("PUT", s)),
  meters: (session?: string | null) =>
    call<Meters>(
      `/api/meters${session ? `?session=${encodeURIComponent(session)}` : ""}`
    ),
  stop: (turn: string) =>
    call<{ ok: boolean }>(`/api/turns/${encodeURIComponent(turn)}/stop`, json("POST", {})),
  approve: (id: string, decision: "allow" | "always" | "deny", reason = "") =>
    call<{ ok: boolean }>(
      `/api/approvals/${id}`,
      json("POST", { decision, reason })
    ),
  upload: async (file: File) => {
    const form = new FormData()
    form.append("file", file)
    return call<{ id: string; name: string; size: number }>("/api/uploads", {
      method: "POST",
      body: form,
    })
  },
}

/** POST a message and yield the server's events as they arrive (Server-Sent Events over fetch). */
export async function* chat(body: {
  session?: string | null
  message: string
  files?: string[]
  hint?: boolean
}) {
  const res = await fetch("/api/chat", json("POST", body))
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // keep the status line
    }
    throw new Error(detail)
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ""
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffer += value
    let cut
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const frame = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      for (const line of frame.split("\n")) {
        if (line.startsWith("data: "))
          yield JSON.parse(line.slice(6)) as ServerEvent
      }
    }
  }
}
