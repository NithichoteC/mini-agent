// Citations the agent writes - "(projects/radio/contract.md > Radio contract > Owner; journal/x.md > Y)" -
// and [[links]] become markdown links on two private schemes, rendered as chips that open the note.
import { createContext } from "react"

export const NoteLinkContext = createContext<{
  openPath: (path: string) => void
  openLink: (name: string) => void
}>({ openPath: () => {}, openLink: () => {} })

export const NOTE = "note:"
export const WIKI = "wikilink:"

const GROUP = /\(([^()\n]*?\.(?:md|txt)\b[^()\n]*)\)/g
const ONE = /^\s*([^;>[\]]+?\.(?:md|txt))\s*(?:>\s*(.+?))?\s*$/
const WIKILINK = /\[\[([^\]|\n]+?)(?:\|([^\]\n]+?))?\]\]/g

const escape = (s: string) => s.replace(/[[\]]/g, "\\$&")

/** Rewrite citations and [[links]] in an answer as links; anything that is not a citation is left alone. */
export function citeLinks(text: string): string {
  const cited = text.replace(GROUP, (whole, inner: string) => {
    const pieces = inner.split(";").map((p) => p.match(ONE))
    if (pieces.some((m) => !m)) return whole // not purely citations: leave the parentheses as written
    return pieces
      .map((m) => {
        const [, path, heading] = m!
        const name = path
          .split("/")
          .pop()!
          .replace(/\.(md|txt)$/, "")
        const where = heading?.split(">").pop()?.trim()
        const label = where && where !== name ? `${name} › ${where}` : name
        return `[${escape(label)}](${NOTE}${encodeURIComponent(path.trim())})`
      })
      .join(" ")
  })
  return cited.replace(
    WIKILINK,
    (_, target: string, label?: string) =>
      `[${escape((label ?? target).trim())}](${WIKI}${encodeURIComponent(target.trim())})`
  )
}
