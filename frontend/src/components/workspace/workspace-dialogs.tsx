// The two questions the file tree asks: a name (new note, new folder, rename) and "really delete?".
import { useState } from "react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field"
import { Input } from "@/components/ui/input"

export type NameAsk = {
  title: string
  description: string
  label: string
  initial: string
  submit: string
  onDone: (name: string) => void
}

/** Mounted with a key per question, so the field starts from `initial` every time it opens. */
function NameForm({ ask, onClose }: { ask: NameAsk; onClose: () => void }) {
  const [name, setName] = useState(ask.initial)
  const clean = name.trim().replace(/^\/+|\/+$/g, "")
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (!clean) return
        onClose()
        ask.onDone(clean)
      }}
    >
      <DialogHeader>
        <DialogTitle>{ask.title}</DialogTitle>
        <DialogDescription>{ask.description}</DialogDescription>
      </DialogHeader>
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor="workspace-name">{ask.label}</FieldLabel>
          <Input
            id="workspace-name"
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            onFocus={(e) => e.target.select()}
          />
        </Field>
      </FieldGroup>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={!clean}>
          {ask.submit}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function NameDialog({
  ask,
  onClose,
}: {
  ask: NameAsk | null
  onClose: () => void
}) {
  return (
    <Dialog open={ask !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        {ask && (
          <NameForm key={ask.title + ask.initial} ask={ask} onClose={onClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}

export function DeleteDialog({
  path,
  onClose,
  onConfirm,
}: {
  path: string | null
  onClose: () => void
  onConfirm: (path: string) => void
}) {
  return (
    <AlertDialog open={path !== null} onOpenChange={(o) => !o && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete {path}?</AlertDialogTitle>
          <AlertDialogDescription>
            It moves to the workspace trash (.aiblue/trash), where it can be
            restored by hand. Search and the graph forget it now.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => path && onConfirm(path)}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
