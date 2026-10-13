// The page's state: which session is open, its turns, and the live turn's events.
// assistant-ui renders it through an external-store runtime; the server owns the truth (trace.db).
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  useExternalStoreRuntime,
  type AppendMessage,
  type AttachmentAdapter,
  type ThreadMessageLike,
} from "@assistant-ui/react"

import {
  api,
  chat,
  type Meters,
  type Part,
  type SessionRow,
  type Transcript,
  type Turn,
} from "@/lib/api"

export const STOPPED: Record<string, string> = {
  max_runs: "ran out of actions (loop.max_runs)",
  blocked: "says it is blocked",
  no_progress: "repeated the same action (no progress)",
}

let counter = 0
const nextId = (prefix: string) => `${prefix}-${Date.now()}-${counter++}`
// a message's id is its position, so reloading the transcript after a turn keeps every id
// (a changed id would look like an edited branch to assistant-ui)
const at = (i: number) => `m-${i}`

function fromTranscript(doc: Transcript): Turn[] {
  const turns: Turn[] = doc.messages.map((m, i) =>
    m.info.role === "user"
      ? {
          id: at(i),
          role: "user",
          kind: m.info.kind,
          text: m.text ?? "",
          attachments: m.attachments ?? [],
        }
      : { id: at(i), role: "assistant", parts: m.parts, status: "done" }
  )
  const last = turns.at(-1)
  if (
    last?.role === "assistant" &&
    doc.info.status &&
    doc.info.status !== "done"
  )
    last.status = doc.info.status
  return turns
}

/** Add one server event to the turns. Pure, so React can replay it. */
function reduce(
  turns: Turn[],
  ev: Awaited<ReturnType<ReturnType<typeof chat>["next"]>>["value"]
): Turn[] {
  if (!ev || typeof ev !== "object") return turns
  const last = turns.at(-1)
  if (!last || last.role !== "assistant") return turns
  const parts = [...(last.parts ?? [])]
  let status = last.status
  if (ev.type === "step") {
    for (const p of ev.parts) {
      const waiting = parts.findIndex(
        (q) =>
          q.type === "tool" &&
          q.approval &&
          q.tool === (p as { tool?: string }).tool
      )
      if (p.type === "tool" && waiting >= 0) parts.splice(waiting, 1, p)
      else parts.push(p)
    }
  } else if (ev.type === "approval") {
    parts.push({
      type: "tool",
      tool: ev.tool,
      title: ev.title,
      run: null,
      approval: { id: ev.id, always: ev.always },
      state: {
        status: "pending",
        decision: "ask",
        input: ev.args,
        output: null,
        duration_ms: null,
      },
    })
  } else if (ev.type === "session.end") {
    status = ev.status
    if (ev.error)
      parts.push({
        type: "error",
        text: ev.error.message,
        model: null,
        step: "llm",
      })
  } else if (ev.type === "error") {
    status = "error"
    parts.push({ type: "error", text: ev.message, model: null, step: "server" })
  }
  return [...turns.slice(0, -1), { ...last, parts, status }]
}

function toThread(turn: Turn): ThreadMessageLike {
  if (turn.role === "user") {
    return {
      id: turn.id,
      role: "user",
      content: [{ type: "text", text: turn.text ?? "" }],
      attachments: (turn.attachments ?? []).map((a, i) => ({
        id: `${turn.id}-f${i}`,
        type: "document" as const,
        name: a.name,
        contentType: "",
        status: { type: "complete" as const },
        content: [{ type: "text" as const, text: a.note }],
      })),
      metadata: { custom: { kind: turn.kind ?? "message" } },
    }
  }
  const content: Exclude<ThreadMessageLike["content"], string>[number][] = []
  const notes: Part[] = []
  turn.parts?.forEach((p, i) => {
    if (p.type === "text") content.push({ type: "text", text: p.text })
    else if (p.type === "reasoning")
      content.push({ type: "reasoning", text: p.text })
    else if (p.type === "tool")
      content.push({
        type: "tool-call",
        toolCallId: `${turn.id}-${i}`,
        toolName: p.tool,
        args: (typeof p.state.input === "object" && p.state.input
          ? p.state.input
          : {}) as never,
        argsText: JSON.stringify(p.state.input ?? {}),
        result: p.state.output ?? undefined,
        isError: p.state.status === "error" || p.state.status === "denied",
      })
    else notes.push(p)
  })
  const running = turn.status === "running"
  return {
    id: turn.id,
    role: "assistant",
    content,
    status: running
      ? { type: "running" }
      : { type: "complete", reason: "stop" },
    metadata: {
      custom: { notes, status: turn.status, parts: turn.parts ?? [] },
    },
  }
}

export function useAgent() {
  const [sessions, setSessions] = useState<SessionRow[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [running, setRunning] = useState(false)
  const [meters, setMeters] = useState<Meters | null>(null)
  const [error, setError] = useState<string | null>(null)
  const live = useRef<string | null>(null) // the session the live turn belongs to
  const turnId = useRef<string | null>(null) // the live turn, for Stop

  const refresh = useCallback(async (sid: string | null) => {
    api.sessions().then(setSessions, (e) => setError(String(e.message ?? e)))
    api.meters(sid).then(setMeters, () => setMeters(null))
  }, [])

  useEffect(() => {
    refresh(null)
  }, [refresh])

  const open = useCallback(
    async (sid: string) => {
      if (running) return
      setError(null)
      try {
        const doc = await api.session(sid)
        setSessionId(doc.info.id)
        setTurns(fromTranscript(doc))
        refresh(doc.info.id)
      } catch (e) {
        setError((e as Error).message)
      }
    },
    [running, refresh]
  )

  const newChat = useCallback(() => {
    if (running) return
    setSessionId(null)
    setTurns([])
    setError(null)
    refresh(null)
  }, [running, refresh])

  const stopped =
    turns.at(-1)?.role === "assistant" ? turns.at(-1)?.status : undefined
  const hintMode = Boolean(sessionId && stopped && stopped in STOPPED)

  const onNew = useCallback(
    async (m: AppendMessage) => {
      const text = m.content
        .filter((p) => p.type === "text")
        .map((p) => (p as { text: string }).text)
        .join("\n")
        .trim()
      const files = (m.attachments ?? []).map((a) => a.id)
      const names = (m.attachments ?? []).map((a) => ({
        name: a.name,
        note: "uploading",
      }))
      const hint = hintMode
      setError(null)
      setRunning(true)
      live.current = sessionId
      setTurns((t) => [
        ...t,
        {
          id: at(t.length),
          role: "user",
          kind: hint ? "hint" : "message",
          text,
          attachments: names,
        },
        {
          id: at(t.length + 1),
          role: "assistant",
          parts: [],
          status: "running",
        },
      ])
      try {
        for await (const ev of chat({
          session: sessionId,
          message: text,
          files,
          hint,
        })) {
          if (ev.type === "turn") turnId.current = ev.turn
          if (ev.type === "session.start") {
            live.current = ev.session
            setSessionId(ev.session)
            api.sessions().then(setSessions, () => {})
          }
          setTurns((t) => reduce(t, ev))
        }
      } catch (e) {
        setTurns((t) =>
          reduce(t, { type: "error", message: (e as Error).message })
        )
      } finally {
        turnId.current = null
        setRunning(false)
        const sid = live.current
        if (sid) {
          // the server's own record of the user message carries each attachment's note
          api.session(sid).then(
            (doc) => setTurns(fromTranscript(doc)),
            () => {}
          )
        }
        refresh(sid)
      }
    },
    [sessionId, hintMode, refresh]
  )

  const attachments = useMemo<AttachmentAdapter>(
    () => ({
      accept: "*",
      async add({ file }) {
        return {
          id: nextId("f"),
          type: file.type.startsWith("image/") ? "image" : "document",
          name: file.name,
          contentType: file.type,
          file,
          status: { type: "requires-action", reason: "composer-send" },
        }
      },
      async send(a) {
        const up = await api.upload(a.file)
        return { ...a, id: up.id, status: { type: "complete" }, content: [] }
      },
      async remove() {},
    }),
    []
  )

  const messages = useMemo(() => turns.map(toThread), [turns])
  // tool cards look up their part (title, decision, approval) by toolCallId = `${turn.id}-${index}`
  const toolParts = useMemo(() => {
    const map = new Map<string, Part>()
    turns.forEach((turn) =>
      turn.parts?.forEach(
        (p, i) => p.type === "tool" && map.set(`${turn.id}-${i}`, p)
      )
    )
    return map
  }, [turns])
  // Stop: the engine ends the turn before its next step; a model call already sent finishes first
  const onCancel = useCallback(async () => {
    if (turnId.current) await api.stop(turnId.current).catch(() => {})
  }, [])

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    isRunning: running,
    convertMessage: (m) => m,
    onNew,
    onCancel,
    adapters: { attachments },
  })

  /** Send the last message again (after a network error or a full request). */
  const retry = useCallback(() => {
    const asked = [...turns].reverse().find((t) => t.role === "user")
    if (!asked || running) return
    onNew({
      content: [{ type: "text", text: asked.text ?? "" }],
      attachments: [],
    } as unknown as AppendMessage)
  }, [turns, running, onNew])

  const approve = useCallback(
    async (
      id: string,
      decision: "allow" | "always" | "deny",
      reason: string
    ) => {
      await api.approve(id, decision, reason)
      setTurns((t) =>
        t.map((turn) =>
          turn.parts?.some((p) => p.type === "tool" && p.approval?.id === id)
            ? {
                ...turn,
                parts: turn.parts.map((p) =>
                  p.type === "tool" && p.approval?.id === id
                    ? { ...p, approval: { ...p.approval, answered: true } }
                    : p
                ),
              }
            : turn
        )
      )
    },
    []
  )

  return {
    runtime,
    toolParts,
    sessions,
    sessionId,
    open,
    newChat,
    running,
    meters,
    error,
    setError,
    hintMode,
    stopped,
    approve,
    retry,
  }
}
