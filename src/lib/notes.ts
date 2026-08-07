export type NoteStatus = 'draft' | 'signed'

export interface ClinicalNote {
  id: string
  patientName: string
  title: string
  /** Subjective / Objective / Assessment / Plan body of the note. */
  body: string
  status: NoteStatus
  createdAt: string
  updatedAt: string
}

export interface NoteDraft {
  patientName: string
  title: string
  body: string
  status?: NoteStatus
}

export const STORAGE_KEY = 'clynotion.notes.v1'

function now(): string {
  return new Date().toISOString()
}

function makeId(): string {
  if (
    typeof crypto !== 'undefined' &&
    typeof crypto.randomUUID === 'function'
  ) {
    return crypto.randomUUID()
  }
  return `note_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

/**
 * Build a new note from a draft. Trims text fields and stamps timestamps.
 * Throws if required fields are missing so callers can surface validation.
 */
export function createNote(draft: NoteDraft): ClinicalNote {
  const patientName = draft.patientName.trim()
  const title = draft.title.trim()
  const body = draft.body.trim()

  if (!patientName) {
    throw new Error('Patient name is required.')
  }
  if (!title) {
    throw new Error('Note title is required.')
  }

  const timestamp = now()
  return {
    id: makeId(),
    patientName,
    title,
    body,
    status: draft.status ?? 'draft',
    createdAt: timestamp,
    updatedAt: timestamp,
  }
}

/** Return notes sorted newest-updated first. */
export function sortNotes(notes: ClinicalNote[]): ClinicalNote[] {
  return [...notes].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
}

export function loadNotes(storage: Storage = localStorage): ClinicalNote[] {
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed as ClinicalNote[]
  } catch {
    return []
  }
}

export function saveNotes(
  notes: ClinicalNote[],
  storage: Storage = localStorage,
): void {
  storage.setItem(STORAGE_KEY, JSON.stringify(notes))
}
