import { useState, type FormEvent } from 'react'

export interface NoteFormProps {
  onCreate: (draft: {
    patientName: string
    title: string
    body: string
  }) => void
}

export function NoteForm({ onCreate }: NoteFormProps) {
  const [patientName, setPatientName] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [error, setError] = useState<string | null>(null)

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError(null)
    try {
      onCreate({ patientName, title, body })
      setPatientName('')
      setTitle('')
      setBody('')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to save note.')
    }
  }

  return (
    <form className="note-form" onSubmit={handleSubmit} noValidate>
      <label className="field">
        <span>Patient name</span>
        <input
          type="text"
          value={patientName}
          onChange={(e) => setPatientName(e.target.value)}
          placeholder="e.g. Jane Doe"
          autoComplete="off"
        />
      </label>

      <label className="field">
        <span>Note title</span>
        <input
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Follow-up visit"
          autoComplete="off"
        />
      </label>

      <label className="field">
        <span>Note</span>
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Subjective, Objective, Assessment, Plan…"
          rows={5}
        />
      </label>

      {error && (
        <p className="note-form__error" role="alert">
          {error}
        </p>
      )}

      <button type="submit" className="button button--primary">
        Save note
      </button>
    </form>
  )
}
