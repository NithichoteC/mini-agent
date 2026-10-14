// What the page shows lives in the URL (?s=session&note=path&view=graph&in=folder), the way ChatGPT keeps
// /c/<id>: a reload, a bookmark or a link opens the same conversation, note and view again.

export type PageState = {
  session: string | null
  note: string | null
  view: "note" | "graph"
  scope: string
}

const KEYS = { session: "s", note: "note", view: "view", scope: "in" } as const

export function readPage(): PageState {
  const q = new URLSearchParams(window.location.search)
  return {
    session: q.get(KEYS.session),
    note: q.get(KEYS.note),
    view: q.get(KEYS.view) === "graph" ? "graph" : "note",
    scope: q.get(KEYS.scope) ?? "",
  }
}

/** Replace (not push) the URL: switching notes should not fill the back button's history. */
export function writePage(state: PageState) {
  const q = new URLSearchParams()
  if (state.session) q.set(KEYS.session, state.session)
  if (state.note) q.set(KEYS.note, state.note)
  if (state.view === "graph") q.set(KEYS.view, "graph")
  if (state.scope) q.set(KEYS.scope, state.scope)
  const next = q.toString() ? `?${q}` : window.location.pathname
  if (
    next !== window.location.search &&
    !(next === window.location.pathname && !window.location.search)
  )
    window.history.replaceState(null, "", next)
}

/** localStorage that never throws (private windows, blocked storage): a layout we cannot keep is fine. */
export const safeStorage = {
  getItem(key: string): string | null {
    try {
      return window.localStorage.getItem(key)
    } catch {
      return null
    }
  },
  setItem(key: string, value: string) {
    try {
      window.localStorage.setItem(key, value)
    } catch {
      // nothing to do: the default layout is used next time
    }
  },
}
