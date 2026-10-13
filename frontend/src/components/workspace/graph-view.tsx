// The workspace as a graph: notes and the [[links]] between them (free, always current), and - once notes
// have been read by the extractor - the entities they mention and the relations between those.
import { useEffect, useMemo, useState } from "react"
import Graph from "graphology"
import forceAtlas2 from "graphology-layout-forceatlas2"
import {
  SigmaContainer,
  useLoadGraph,
  useRegisterEvents,
  useSetSettings,
} from "@react-sigma/core"
import "@react-sigma/core/lib/style.css"
import { BookOpenTextIcon, NetworkIcon, XIcon } from "lucide-react"

import type { GraphData, GraphEdge } from "@/lib/workspace"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Empty,
  EmptyContent,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

export type GraphViewProps = {
  data: GraphData | null
  onOpenNote(path: string): void
  onBuild(): void
  building: boolean
  progress?: { done: number; total: number; tokens: number } | null
}

type Show = "links" | "entities" | "relations"
type Colors = {
  note: string
  entity: string
  edge: string
  label: string
  dim: string
}

/** A theme variable as rgb(): the theme is oklch(), which sigma's WebGL renderer cannot read. */
function cssColor(name: string): string {
  const ctx = document.createElement("canvas").getContext("2d")
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  if (!ctx || !value) return "#888888"
  ctx.fillStyle = value
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data
  return `rgb(${r}, ${g}, ${b})`
}

const readColors = (): Colors => ({
  note: cssColor("--chart-1"),
  entity: cssColor("--chart-2"),
  edge: cssColor("--border"),
  label: cssColor("--foreground"),
  dim: cssColor("--muted"),
})

/** Colors that follow the light / dark switch (the `dark` class on <html>). */
function useColors() {
  const [colors, setColors] = useState(readColors)
  useEffect(() => {
    const watch = new MutationObserver(() => setColors(readColors()))
    watch.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    })
    return () => watch.disconnect()
  }, [])
  return colors
}

function buildGraph(data: GraphData, show: Show[], colors: Colors) {
  const g = new Graph({ type: "directed", multi: true })
  const entities = show.includes("entities")
  data.nodes.forEach((n, i) => {
    if (n.kind === "entity" && !entities) return
    const angle = (i / data.nodes.length) * 2 * Math.PI // a circle to start from; the layout spreads it
    g.addNode(n.id, {
      label: n.label,
      kind: n.kind,
      path: n.path,
      x: Math.cos(angle),
      y: Math.sin(angle),
      size:
        n.kind === "note"
          ? 6 + 2 * Math.sqrt(n.degree)
          : 3 + 1.5 * Math.sqrt(n.degree),
      color: n.kind === "note" ? colors.note : colors.entity,
    })
  })
  data.edges.forEach((e) => {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) return
    if (e.kind === "link" && !show.includes("links")) return
    if (e.kind === "relation" && !show.includes("relations")) return
    g.addEdge(e.source, e.target, {
      kind: e.kind,
      label: e.label,
      color: colors.edge,
      size: e.kind === "mention" ? 0.5 : 1,
      type: e.kind === "mention" ? "line" : "arrow",
    })
  })
  if (g.order > 1)
    forceAtlas2.assign(g, {
      iterations: 150,
      settings: forceAtlas2.inferSettings(g),
    })
  return g
}

/** Inside SigmaContainer: loads the graph, dims what is not next to the hovered node, handles clicks. */
function Scene({
  graph,
  colors,
  onNote,
  onEntity,
}: {
  graph: Graph
  colors: Colors
  onNote: (path: string) => void
  onEntity: (id: string) => void
}) {
  const load = useLoadGraph()
  const events = useRegisterEvents()
  const settings = useSetSettings()
  const [hovered, setHovered] = useState<string | null>(null)

  useEffect(() => load(graph), [load, graph])

  useEffect(() => {
    events({
      enterNode: ({ node }) => setHovered(node),
      leaveNode: () => setHovered(null),
      clickNode: ({ node }) => {
        const attrs = graph.getNodeAttributes(node)
        if (attrs.kind === "note" && attrs.path) onNote(attrs.path as string)
        else onEntity(node)
      },
    })
  }, [events, graph, onNote, onEntity])

  useEffect(() => {
    const near = hovered
      ? new Set([hovered, ...graph.neighbors(hovered)])
      : null
    settings({
      labelColor: { color: colors.label },
      nodeReducer: (node, attrs) =>
        near && !near.has(node)
          ? { ...attrs, color: colors.dim, label: "" }
          : attrs,
      edgeReducer: (edge, attrs) =>
        near &&
        !(graph.source(edge) === hovered || graph.target(edge) === hovered)
          ? { ...attrs, hidden: true }
          : attrs,
    })
  }, [hovered, graph, settings, colors])

  return null
}

function EntityCard({
  label,
  edges,
  onNote,
  onClose,
}: {
  label: string
  edges: GraphEdge[]
  onNote: (path: string) => void
  onClose: () => void
}) {
  const name = (id: string) => id.replace(/^entity:/, "")
  return (
    <Card size="sm" className="absolute top-3 right-3 w-80">
      <CardHeader>
        <div className="flex items-start gap-2">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <CardTitle>
              <span className="block truncate">{label}</span>
            </CardTitle>
            <CardDescription>
              {edges.length} relation{edges.length === 1 ? "" : "s"}
            </CardDescription>
          </div>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Close"
            onClick={onClose}
          >
            <XIcon />
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <ScrollArea className="max-h-72">
          <ul className="flex flex-col gap-3 text-sm">
            {edges.map((e, i) => (
              <li key={i} className="flex flex-col gap-0.5">
                <span>
                  {name(e.source)}{" "}
                  <span className="text-muted-foreground">—{e.label}→</span>{" "}
                  {name(e.target)}
                </span>
                {e.evidence && (
                  <span className="text-xs text-muted-foreground">
                    {e.evidence}
                  </span>
                )}
                {e.path && (
                  <Button
                    variant="link"
                    size="xs"
                    className="self-start"
                    onClick={() => onNote(e.path!)}
                  >
                    {e.path}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </ScrollArea>
      </CardContent>
    </Card>
  )
}

export function GraphView({
  data,
  onOpenNote,
  onBuild,
  building,
  progress,
}: GraphViewProps) {
  const [show, setShow] = useState<Show[]>(["links", "entities", "relations"])
  const [entity, setEntity] = useState<string | null>(null)
  const colors = useColors()
  const graph = useMemo(
    () => (data ? buildGraph(data, show, colors) : null),
    [data, show, colors]
  )

  if (!data || !graph)
    return (
      <div className="flex h-full flex-col gap-3 p-3">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="flex-1" />
      </div>
    )

  const { stats } = data
  const build = (stats.pending > 0 || building) && (
    <Button variant="outline" size="sm" onClick={onBuild} disabled={building}>
      {building ? (
        <Spinner data-icon="inline-start" />
      ) : (
        <BookOpenTextIcon data-icon="inline-start" />
      )}
      {building
        ? `Reading ${progress?.done ?? 0}/${progress?.total ?? stats.pending} · ${(progress?.tokens ?? 0).toLocaleString()} tokens`
        : `Read ${stats.pending} new section${stats.pending === 1 ? "" : "s"}`}
    </Button>
  )
  const selected = entity ? data.nodes.find((n) => n.id === entity) : undefined

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2">
        <ToggleGroup
          type="multiple"
          variant="outline"
          size="sm"
          value={show}
          onValueChange={(v) => setShow(v as Show[])}
        >
          <ToggleGroupItem value="links">Links</ToggleGroupItem>
          <ToggleGroupItem value="entities">Entities</ToggleGroupItem>
          <ToggleGroupItem value="relations">Relations</ToggleGroupItem>
        </ToggleGroup>
        <Badge variant="secondary">{stats.notes} notes</Badge>
        <Badge variant="secondary">{stats.entities} entities</Badge>
        <Badge variant="secondary">{stats.relations} relations</Badge>
        <div className="flex-1" />
        {build}
      </div>
      <div className="relative min-h-0 flex-1">
        {data.nodes.length === 0 ? (
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <NetworkIcon />
              </EmptyMedia>
              <EmptyTitle>No graph yet</EmptyTitle>
              <EmptyDescription>
                Notes linked with [[note name]] appear here; reading notes adds
                the people, projects and facts they mention.
              </EmptyDescription>
            </EmptyHeader>
            {build && <EmptyContent>{build}</EmptyContent>}
          </Empty>
        ) : (
          <>
            <SigmaContainer
              className="size-full"
              settings={{
                renderEdgeLabels: false,
                labelRenderedSizeThreshold: 6,
                defaultEdgeType: "arrow",
              }}
            >
              <Scene
                graph={graph}
                colors={colors}
                onNote={onOpenNote}
                onEntity={setEntity}
              />
            </SigmaContainer>
            {selected && (
              <EntityCard
                label={selected.label}
                edges={data.edges.filter(
                  (e) =>
                    e.kind === "relation" &&
                    (e.source === selected.id || e.target === selected.id)
                )}
                onNote={onOpenNote}
                onClose={() => setEntity(null)}
              />
            )}
          </>
        )}
      </div>
    </div>
  )
}
