// The workspace as a graph: notes and the [[links]] between them (free, always current), and - once notes
// have been read by the extractor - the entities they mention and the relations between those.
import { useEffect, useMemo, useState } from "react"
import Graph from "graphology"
import forceAtlas2 from "graphology-layout-forceatlas2"
import {
  SigmaContainer,
  useCamera,
  useLoadGraph,
  useRegisterEvents,
  useSetSettings,
  useSigma,
} from "@react-sigma/core"
import "@react-sigma/core/lib/style.css"
import {
  BookOpenTextIcon,
  FocusIcon,
  MinusIcon,
  NetworkIcon,
  PlusIcon,
  XIcon,
} from "lucide-react"

import type { GraphData, GraphEdge } from "@/lib/workspace"
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
import { Separator } from "@/components/ui/separator"
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
  link: string
  mention: string
  relation: string
  label: string
  faded: string
  surface: string
  border: string
}

/** A theme variable as rgba(): the theme is oklch(), which sigma's WebGL renderer cannot read. */
function cssColor(name: string, alpha = 1): string {
  const ctx = document.createElement("canvas").getContext("2d")
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  if (!ctx || !value) return `rgba(128, 128, 128, ${alpha})`
  ctx.fillStyle = value
  ctx.fillRect(0, 0, 1, 1)
  const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data
  return `rgba(${r}, ${g}, ${b}, ${((a / 255) * alpha).toFixed(3)})`
}

/** `share` of the foreground over the background, as a solid colour (node colours ignore alpha). */
function blend(share: number): string {
  const rgb = (v: string) => (v.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number)
  const [f, b] = [rgb(cssColor("--foreground")), rgb(cssColor("--background"))]
  const mix = f.map((c, i) => Math.round(c * share + b[i] * (1 - share)))
  return `rgb(${mix.join(", ")})`
}

const readColors = (): Colors => ({
  note: cssColor("--graph-note"),
  entity: cssColor("--graph-entity"),
  edge: cssColor("--graph-edge"),
  link: cssColor("--graph-note", 0.55), // note -> note: the structure the user wrote, strongest
  mention: cssColor("--graph-edge"), // note -> entity: context, quiet
  relation: cssColor("--graph-relation"), // entity -> entity: what the notes say
  label: cssColor("--foreground", 0.85),
  faded: blend(0.2),
  surface: cssColor("--popover"),
  border: cssColor("--border"),
})

type HoverData = {
  x: number
  y: number
  size: number
  label?: string | null
  color: string
}

/** The hovered node's label on a theme-coloured pill (sigma's own draws a white box, wrong in dark mode). */
function hoverDrawer(colors: Colors) {
  return (
    ctx: CanvasRenderingContext2D,
    data: HoverData,
    settings: { labelSize: number; labelFont: string; labelWeight: string }
  ) => {
    const label = data.label ?? ""
    const size = settings.labelSize
    ctx.font = `600 ${size}px ${settings.labelFont}`
    const width = ctx.measureText(label).width
    const pad = 5
    const left = data.x - data.size - pad
    const top = data.y - Math.max(data.size, size / 2) - pad
    const height = Math.max(data.size * 2, size) + pad * 2
    ctx.beginPath()
    ctx.roundRect(
      left,
      top,
      data.size * 2 + width + pad * 4,
      height,
      height / 2
    )
    ctx.fillStyle = colors.surface
    ctx.shadowColor = "rgba(0, 0, 0, 0.12)"
    ctx.shadowBlur = 8
    ctx.fill()
    ctx.shadowBlur = 0
    ctx.strokeStyle = colors.border
    ctx.lineWidth = 1
    ctx.stroke()
    ctx.fillStyle = colors.label
    ctx.fillText(label, data.x + data.size + pad * 1.5, data.y + size / 3)
  }
}

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
    const note = n.kind === "note"
    g.addNode(n.id, {
      // long entity names ("2569 budget line for communications, 4.2 million baht") are cut on the
      // canvas, where they would cover their neighbours; hover and the entity card show them whole
      label:
        note || n.label.length <= 24
          ? n.label
          : `${n.label.slice(0, 22).trimEnd()}…`,
      full: n.label,
      kind: n.kind,
      path: n.path,
      x: Math.cos(angle) * 10,
      y: Math.sin(angle) * 10,
      // notes are big enough to be labelled; entities get a label on hover or when zoomed in
      size: note ? 7 + 1.5 * Math.sqrt(n.degree) : 3.5 + Math.sqrt(n.degree),
      color: note ? colors.note : colors.entity,
      zIndex: note ? 2 : 1,
    })
  })
  data.edges.forEach((e) => {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) return
    if (e.kind === "link" && !show.includes("links")) return
    if (e.kind === "relation" && !show.includes("relations")) return
    g.addEdge(e.source, e.target, {
      kind: e.kind,
      label: e.label,
      color:
        e.kind === "link"
          ? colors.link
          : e.kind === "relation"
            ? colors.relation
            : colors.mention,
      size: e.kind === "link" ? 2 : e.kind === "relation" ? 1.2 : 0.5,
    })
  })
  if (g.order > 1)
    forceAtlas2.assign(g, {
      iterations: 400,
      settings: {
        ...forceAtlas2.inferSettings(g),
        gravity: 4,
        strongGravityMode: true, // keeps small islands (a note with no links) near the rest
        scalingRatio: 4,
        linLogMode: true, // tighter clusters around each note
        barnesHutOptimize: g.order > 300,
      },
    })
  return g
}

// labels hang to the right of their dots: frame the graph a little to the right and a little wider than
// its dots, so the rightmost labels are not cut by the panel edge
const FIT = { x: 0.55, y: 0.5, ratio: 1.12, angle: 0 }

/** Inside SigmaContainer: loads the graph, fades what is not next to the hovered node, handles clicks. */
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
  const sigma = useSigma()
  const [hovered, setHovered] = useState<string | null>(null)

  useEffect(() => {
    load(graph)
    sigma.getCamera().setState(FIT)
  }, [load, graph, sigma])

  // the panels settle (and get dragged) after the canvas is created: follow the container's size
  useEffect(() => {
    const box = sigma.getContainer()
    // redraw at the new size; the camera is relative to the canvas, so the view (and any zoom the person
    // chose) stays where it was instead of snapping back to the fit
    let frame = 0
    const watch = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        sigma.resize()
        sigma.refresh()
      })
    })
    watch.observe(box)
    return () => {
      cancelAnimationFrame(frame)
      watch.disconnect()
    }
  }, [sigma])

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
      defaultDrawNodeHover: hoverDrawer(colors),
      nodeReducer: (node, attrs) => {
        if (!near) return attrs
        if (!near.has(node))
          return { ...attrs, color: colors.faded, label: "", zIndex: 0 }
        return {
          ...attrs,
          forceLabel: true,
          label: (attrs.full as string) ?? attrs.label,
          highlighted: node === hovered,
          zIndex: 3,
        }
      },
      edgeReducer: (edge, attrs) => {
        if (!near) return attrs
        const touches =
          graph.source(edge) === hovered || graph.target(edge) === hovered
        return touches
          ? { ...attrs, size: (attrs.size as number) + 0.8 }
          : { ...attrs, hidden: true }
      },
    })
  }, [hovered, graph, settings, colors])

  return null
}

function Controls() {
  const { zoomIn, zoomOut } = useCamera({ duration: 200 })
  const sigma = useSigma()
  const reset = () => sigma.getCamera().animate(FIT, { duration: 250 })
  return (
    <div className="absolute right-3 bottom-3 flex flex-col overflow-hidden rounded-lg border bg-background shadow-sm">
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Zoom in"
        onClick={() => zoomIn()}
      >
        <PlusIcon />
      </Button>
      <Separator />
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Zoom out"
        onClick={() => zoomOut()}
      >
        <MinusIcon />
      </Button>
      <Separator />
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label="Fit the graph"
        onClick={() => reset()}
      >
        <FocusIcon />
      </Button>
    </div>
  )
}

function Legend({ entities }: { entities: boolean }) {
  return (
    <div className="absolute bottom-3 left-3 flex items-center gap-3 rounded-lg border bg-background px-3 py-1.5 text-xs text-muted-foreground shadow-sm">
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-full bg-graph-note" />
        Note
      </span>
      {entities && (
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-full bg-graph-entity" />
          Entity
        </span>
      )}
      <span>Hover to focus · click to open</span>
    </div>
  )
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
              {edges.length} relation{edges.length === 1 ? "" : "s"} read from
              the notes
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
                  <span className="text-muted-foreground">{e.label}</span>{" "}
                  {name(e.target)}
                </span>
                {e.evidence && (
                  <span className="text-xs text-muted-foreground">
                    “{e.evidence}”
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
        ? `Reading ${progress?.done ?? 0} of ${progress?.total ?? stats.pending} · ${(progress?.tokens ?? 0).toLocaleString()} tokens`
        : `Read ${stats.pending} new section${stats.pending === 1 ? "" : "s"}`}
    </Button>
  )
  const selected = entity ? data.nodes.find((n) => n.id === entity) : undefined

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-3 border-b px-3 py-2">
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
        <span className="min-w-0 truncate text-xs text-muted-foreground">
          {stats.notes} notes · {stats.links} links · {stats.entities} entities
          · {stats.relations} relations
        </span>
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
          <SigmaContainer
            className="graph-canvas size-full"
            settings={{
              defaultEdgeType: "line",
              renderEdgeLabels: false,
              labelFont: "Geist Variable, sans-serif",
              labelSize: 12,
              labelWeight: "500",
              labelRenderedSizeThreshold: 4,
              labelDensity: 0.45,
              labelGridCellSize: 90,
              stagePadding: 90,
              zIndex: true,
              minCameraRatio: 0.1,
              maxCameraRatio: 4,
              // sigma animates every wheel step for 250 ms and zooms 1.7x a step: each new step restarts the
              // animation, so the view trails the wheel. Short animations and finer steps follow it.
              zoomDuration: 80,
              zoomingRatio: 1.35,
              doubleClickZoomingDuration: 150,
              inertiaDuration: 120,
            }}
          >
            <Scene
              graph={graph}
              colors={colors}
              onNote={onOpenNote}
              onEntity={setEntity}
            />
            <Controls />
            <Legend entities={show.includes("entities")} />
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
          </SigmaContainer>
        )}
      </div>
    </div>
  )
}
