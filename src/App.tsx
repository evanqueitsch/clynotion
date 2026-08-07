import { useEffect, useMemo, useState } from 'react'
import './App.css'
import {
  createNote,
  loadNotes,
  saveNotes,
  sortNotes,
  type ClinicalNote,
} from './lib/notes'
import { NoteForm } from './components/NoteForm'
import { NoteList } from './components/NoteList'

function App() {
  const [notes, setNotes] = useState<ClinicalNote[]>(() => loadNotes())
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    saveNotes(notes)
  }, [notes])

  const sorted = useMemo(() => sortNotes(notes), [notes])

  function handleCreate(draft: {
    patientName: string
    title: string
    body: string
  }) {
    const note = createNote(draft)
    setNotes((prev) => [note, ...prev])
    setSelectedId(note.id)
  }

  function handleDelete(id: string) {
    setNotes((prev) => prev.filter((n) => n.id !== id))
    setSelectedId((prev) => (prev === id ? null : prev))
  }

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__brand">
          <span className="app__logo" aria-hidden="true">
            +
          </span>
          <div>
            <h1>clynotion</h1>
            <p>Clinical notation</p>
          </div>
        </div>
        <span className="app__count" aria-live="polite">
          {notes.length} {notes.length === 1 ? 'note' : 'notes'}
        </span>
      </header>

      <main className="app__main">
        <section className="app__panel" aria-label="New clinical note">
          <h2>New note</h2>
          <NoteForm onCreate={handleCreate} />
        </section>

        <section className="app__panel" aria-label="Saved clinical notes">
          <h2>Notes</h2>
          <NoteList
            notes={sorted}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onDelete={handleDelete}
          />
        </section>
      </main>
    </div>
  )
}

export default App
