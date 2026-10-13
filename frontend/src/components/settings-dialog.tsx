import { useEffect, useState } from "react"
import { PlusIcon, SettingsIcon, TrashIcon } from "lucide-react"
import { toast } from "sonner"

import { api, type Settings, type SettingsDoc } from "@/lib/api"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Field,
  FieldContent,
  FieldDescription,
  FieldGroup,
  FieldLabel,
  FieldLegend,
  FieldSet,
} from "@/components/ui/field"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Spinner } from "@/components/ui/spinner"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"

function Choice({
  id,
  value,
  options,
  onChange,
}: {
  id: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger id={id} className="w-full sm:w-64">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectContent>
    </Select>
  )
}

function NumberField({
  id,
  label,
  description,
  value,
  onChange,
}: {
  id: string
  label: string
  description: string
  value: number
  onChange: (v: number) => void
}) {
  return (
    <Field orientation="horizontal">
      <FieldContent>
        <FieldLabel htmlFor={id}>{label}</FieldLabel>
        <FieldDescription>{description}</FieldDescription>
      </FieldContent>
      <Input
        id={id}
        type="number"
        className="w-28"
        value={Number.isNaN(value) ? "" : value}
        onChange={(e) => onChange(e.target.valueAsNumber)}
      />
    </Field>
  )
}

/** Edits config/workflow.yaml and config/runtime.yaml through the server (validated, comments kept). */
export function SettingsDialog({ disabled }: { disabled?: boolean }) {
  const [open, setOpen] = useState(false)
  const [doc, setDoc] = useState<SettingsDoc | null>(null)
  const [draft, setDraft] = useState<Settings | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!open) return
    api.settings().then(
      (d) => {
        setDoc(d)
        setDraft(structuredClone(d.values))
      },
      (e) => setError(e.message)
    )
  }, [open])

  const set = (patch: Partial<Settings>) =>
    setDraft((d) => (d ? { ...d, ...patch } : d))

  async function save() {
    if (!draft) return
    setSaving(true)
    setError(null)
    try {
      await api.saveSettings(draft)
      toast.success("Settings saved to the yaml files")
      setOpen(false)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  const models =
    doc?.options.models.map((m) => ({
      value: m.key,
      label: `${m.key} · ${m.model}`,
    })) ?? []

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) {
          setError(null)
          setDraft(null)
        }
        setOpen(next)
      }}
    >
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Settings"
          disabled={disabled}
        >
          <SettingsIcon />
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>
            Saved into {doc?.files.join(" and ") ?? "the yaml files"} - the
            command line uses the same files.
          </DialogDescription>
        </DialogHeader>
        {!draft || !doc ? (
          <div className="flex justify-center py-10">
            {error ? <p className="text-sm">{error}</p> : <Spinner />}
          </div>
        ) : (
          <Tabs defaultValue="models">
            <TabsList>
              <TabsTrigger value="models">Models</TabsTrigger>
              <TabsTrigger value="tools">Tools</TabsTrigger>
              <TabsTrigger value="permissions">Permissions</TabsTrigger>
              <TabsTrigger value="rag">Retrieval</TabsTrigger>
              <TabsTrigger value="loop">Loop</TabsTrigger>
            </TabsList>
            <div className="max-h-96 overflow-y-auto py-2 pr-1">
              <TabsContent value="models">
                <FieldGroup>
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel htmlFor="actor">Actor</FieldLabel>
                      <FieldDescription>
                        Picks the actions (roles.actor).
                      </FieldDescription>
                    </FieldContent>
                    <Choice
                      id="actor"
                      value={draft.actor}
                      options={models}
                      onChange={(v) => set({ actor: v })}
                    />
                  </Field>
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel htmlFor="reviewer">Reviewer</FieldLabel>
                      <FieldDescription>
                        Judges the answer (roles.reviewer).
                      </FieldDescription>
                    </FieldContent>
                    <Choice
                      id="reviewer"
                      value={draft.reviewer}
                      options={models}
                      onChange={(v) => set({ reviewer: v })}
                    />
                  </Field>
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel id="actions-label">
                        Action protocol
                      </FieldLabel>
                      <FieldDescription>
                        json_text: a ```json block; tool_calls: native function
                        calling.
                      </FieldDescription>
                    </FieldContent>
                    <ToggleGroup
                      type="single"
                      variant="outline"
                      aria-labelledby="actions-label"
                      value={draft.actions}
                      onValueChange={(v) => v && set({ actions: v })}
                    >
                      {doc.options.actions.map((a) => (
                        <ToggleGroupItem key={a} value={a}>
                          {a}
                        </ToggleGroupItem>
                      ))}
                    </ToggleGroup>
                  </Field>
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel id="review-label">Review</FieldLabel>
                      <FieldDescription>
                        auto: the reviewer checks a turn that used a tool;
                        always: every answer; never: no review.
                      </FieldDescription>
                    </FieldContent>
                    <ToggleGroup
                      type="single"
                      variant="outline"
                      aria-labelledby="review-label"
                      value={draft.review}
                      onValueChange={(v) => v && set({ review: v })}
                    >
                      {doc.options.review.map((a) => (
                        <ToggleGroupItem key={a} value={a}>
                          {a}
                        </ToggleGroupItem>
                      ))}
                    </ToggleGroup>
                  </Field>
                </FieldGroup>
              </TabsContent>

              <TabsContent value="tools">
                <FieldSet>
                  <FieldLegend variant="label">Enabled tools</FieldLegend>
                  <FieldDescription>
                    What the model is told it can use. final_answer is always
                    there.
                  </FieldDescription>
                  <FieldGroup>
                    {doc.options.tools.map((t) => (
                      <Field key={t.name} orientation="horizontal">
                        <Switch
                          id={`tool-${t.name}`}
                          checked={draft.tools.includes(t.name)}
                          onCheckedChange={(on) =>
                            set({
                              tools: on
                                ? doc.options.tools
                                    .map((x) => x.name)
                                    .filter(
                                      (n) =>
                                        n === t.name || draft.tools.includes(n)
                                    )
                                : draft.tools.filter((n) => n !== t.name),
                            })
                          }
                        />
                        <FieldContent>
                          <FieldLabel htmlFor={`tool-${t.name}`}>
                            {t.name}
                            <Badge variant="outline">{t.permission}</Badge>
                          </FieldLabel>
                          <FieldDescription>{t.description}</FieldDescription>
                        </FieldContent>
                      </Field>
                    ))}
                  </FieldGroup>
                </FieldSet>
              </TabsContent>

              <TabsContent value="permissions">
                <FieldSet>
                  <FieldLegend variant="label">Permission rules</FieldLegend>
                  <FieldDescription>
                    On top of each tool's default (badge in Tools). Tool and
                    pattern are globs; the last matching rule wins.
                  </FieldDescription>
                  <FieldGroup>
                    {draft.permissions.map((p, i) => {
                      const update = (patch: Partial<typeof p>) =>
                        set({
                          permissions: draft.permissions.map((q, j) =>
                            j === i ? { ...q, ...patch } : q
                          ),
                        })
                      return (
                        <Field key={i} orientation="horizontal">
                          <Input
                            aria-label="tool"
                            className="w-32"
                            value={p.tool}
                            onChange={(e) => update({ tool: e.target.value })}
                          />
                          <Input
                            aria-label="pattern"
                            value={p.pattern}
                            onChange={(e) =>
                              update({ pattern: e.target.value })
                            }
                          />
                          <Select
                            value={p.action}
                            onValueChange={(v) => update({ action: v })}
                          >
                            <SelectTrigger aria-label="action" className="w-28">
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectGroup>
                                {doc.options.permission_actions.map((a) => (
                                  <SelectItem key={a} value={a}>
                                    {a}
                                  </SelectItem>
                                ))}
                              </SelectGroup>
                            </SelectContent>
                          </Select>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label="Remove rule"
                            onClick={() =>
                              set({
                                permissions: draft.permissions.filter(
                                  (_, j) => j !== i
                                ),
                              })
                            }
                          >
                            <TrashIcon />
                          </Button>
                        </Field>
                      )
                    })}
                    <div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          set({
                            permissions: [
                              ...draft.permissions,
                              { tool: "bash", pattern: "*", action: "ask" },
                            ],
                          })
                        }
                      >
                        <PlusIcon data-icon="inline-start" />
                        Add rule
                      </Button>
                    </div>
                  </FieldGroup>
                </FieldSet>
              </TabsContent>

              <TabsContent value="rag">
                <FieldGroup>
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel htmlFor="rag-type">Retrieval type</FieldLabel>
                      <FieldDescription>
                        How rag_search finds passages in long attachments.
                      </FieldDescription>
                    </FieldContent>
                    <Choice
                      id="rag-type"
                      value={draft.rag.type}
                      options={doc.options.rag_types.map((t) => ({
                        value: t,
                        label: t,
                      }))}
                      onChange={(v) => set({ rag: { ...draft.rag, type: v } })}
                    />
                  </Field>
                  <NumberField
                    id="rag-k"
                    label="Passages (k)"
                    description="Passages per rag_search."
                    value={draft.rag.k}
                    onChange={(v) => set({ rag: { ...draft.rag, k: v } })}
                  />
                  <NumberField
                    id="rag-full"
                    label="Full-text limit"
                    description="Tokens; a shorter attachment goes into the message whole."
                    value={draft.rag.full_text_tokens}
                    onChange={(v) =>
                      set({ rag: { ...draft.rag, full_text_tokens: v } })
                    }
                  />
                  <Field orientation="horizontal">
                    <FieldContent>
                      <FieldLabel htmlFor="rag-ocr">OCR</FieldLabel>
                      <FieldDescription>
                        Read pages with no text layer through the ocr role (uses
                        tokens).
                      </FieldDescription>
                    </FieldContent>
                    <Switch
                      id="rag-ocr"
                      checked={draft.rag.ocr}
                      onCheckedChange={(on) =>
                        set({ rag: { ...draft.rag, ocr: on } })
                      }
                    />
                  </Field>
                </FieldGroup>
              </TabsContent>

              <TabsContent value="loop">
                <FieldGroup>
                  <NumberField
                    id="max-runs"
                    label="Max actions"
                    description="loop.max_runs: actions per message before asking you for a hint."
                    value={draft.loop.max_runs}
                    onChange={(v) =>
                      set({ loop: { ...draft.loop, max_runs: v } })
                    }
                  />
                  <NumberField
                    id="max-repeats"
                    label="Max repeats"
                    description="loop.max_repeats: the same action this many times = no progress."
                    value={draft.loop.max_repeats}
                    onChange={(v) =>
                      set({ loop: { ...draft.loop, max_repeats: v } })
                    }
                  />
                </FieldGroup>
              </TabsContent>
            </div>
          </Tabs>
        )}
        {error && draft && (
          <Alert variant="destructive">
            <AlertTitle>Not saved</AlertTitle>
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button onClick={save} disabled={!draft || saving}>
            {saving && <Spinner data-icon="inline-start" />}
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
