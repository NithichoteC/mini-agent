import { GaugeIcon } from "lucide-react"

import type { Meter, Meters } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Progress } from "@/components/ui/progress"
import { Separator } from "@/components/ui/separator"

const fmt = (n: number) => n.toLocaleString("en-US")

function Row({
  label,
  meter,
  cap,
}: {
  label: string
  meter: Meter
  cap: number | null | undefined
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-4 text-sm">
        <span className="truncate font-medium">{label}</span>
        <span className="text-muted-foreground tabular-nums">
          {fmt(meter.used)}
          {cap ? ` / ${fmt(cap)}` : ""}
          {meter.percent !== null ? ` · ${meter.percent}%` : ""}
        </span>
      </div>
      <Progress value={Math.min(100, meter.percent ?? 0)} aria-label={label} />
    </div>
  )
}

/** Context used by this conversation and today's tokens per model, from the trace. */
export function MetersButton({ meters }: { meters: Meters | null }) {
  const ctx = meters?.context
  const top = meters?.daily.reduce<Meter | null>(
    (a, b) => ((b.percent ?? 0) > (a?.percent ?? -1) ? b : a),
    null
  )
  return (
    <HoverCard openDelay={100}>
      <HoverCardTrigger asChild>
        <Button variant="ghost" size="sm" aria-label="Token meters">
          <GaugeIcon data-icon="inline-start" />
          <span className="tabular-nums">
            {ctx?.percent != null ? `ctx ${ctx.percent}%` : "ctx –"}
            {top?.percent != null ? ` · day ${top.percent}%` : ""}
          </span>
        </Button>
      </HoverCardTrigger>
      <HoverCardContent align="end" className="w-80">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-3">
            <p className="text-sm font-medium">Last request</p>
            {ctx ? (
              <>
                <Row label={ctx.model} meter={ctx} cap={ctx.window} />
                {ctx.request_tokens && (
                  <p className="text-xs text-muted-foreground">
                    Measured against the plan's limit for one request (
                    {fmt(ctx.request_tokens)} tokens), not the model's
                    {ctx.context_window
                      ? ` ${fmt(ctx.context_window)}-token window`
                      : " window"}
                    . Older tool output is trimmed to stay under it.
                  </p>
                )}
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                No agent step in this conversation yet.
              </p>
            )}
          </div>
          <Separator />
          <div className="flex flex-col gap-3">
            <p className="text-sm font-medium">Tokens today, per model</p>
            {meters?.daily.map((m) => (
              <Row key={m.model} label={m.model} meter={m} cap={m.cap} />
            ))}
            <p className="text-xs text-muted-foreground">{meters?.note}</p>
          </div>
        </div>
      </HoverCardContent>
    </HoverCard>
  )
}
