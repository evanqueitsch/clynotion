import type { ClinicalNote } from '../lib/notes'

export interface NoteListProps {
  notes: ClinicalNote[]
  selectedId: string | null
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

function formatTimestamp(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

export function NoteList({
  notes,
  selectedId,
  onSelect,
  onDelete,
}: NoteListProps) {
  if (notes.length === 0) {
    return (
      <p className="note-list__empty">
        No notes yet. Create your first clinical note using the form.
      </p>
    )
  }

  return (
    <ul className="note-list">
      {notes.map((note) => {
        const isSelected = note.id === selectedId
        return (
          <li
            key={note.id}
            className={`note-card${isSelected ? ' note-card--selected' : ''}`}
          >
            <button
              type="button"
              className="note-card__main"
              onClick={() => onSelect(note.id)}
              aria-expanded={isSelected}
            >
              <div className="note-card__head">
                <span className="note-card__title">{note.title}</span>
                <span className={`badge badge--${note.status}`}>
                  {note.status}
                </span>
              </div>
              <div className="note-card__meta">
                <span className="note-card__patient">{note.patientName}</span>
                <time dateTime={note.updatedAt}>
                  {formatTimestamp(note.updatedAt)}
                </time>
              </div>
              {isSelected && note.body && (
                <p className="note-card__body">{note.body}</p>
              )}
            </button>
            <button
              type="button"
              className="button button--ghost note-card__delete"
              onClick={() => onDelete(note.id)}
              aria-label={`Delete note: ${note.title}`}
            >
              Delete
            </button>
          </li>
        )
      })}
    </ul>
  )
}
