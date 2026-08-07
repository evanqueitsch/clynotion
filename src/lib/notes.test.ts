import { describe, it, expect, beforeEach } from 'vitest'
import {
  createNote,
  loadNotes,
  saveNotes,
  sortNotes,
  STORAGE_KEY,
  type ClinicalNote,
} from './notes'

describe('createNote', () => {
  it('trims fields and stamps timestamps', () => {
    const note = createNote({
      patientName: '  Jane Doe  ',
      title: '  Follow-up visit  ',
      body: '  Patient reports improvement.  ',
    })

    expect(note.patientName).toBe('Jane Doe')
    expect(note.title).toBe('Follow-up visit')
    expect(note.body).toBe('Patient reports improvement.')
    expect(note.status).toBe('draft')
    expect(note.id).toBeTruthy()
    expect(note.createdAt).toBe(note.updatedAt)
  })

  it('throws when the patient name is missing', () => {
    expect(() =>
      createNote({ patientName: '   ', title: 'x', body: '' }),
    ).toThrow(/patient name/i)
  })

  it('throws when the title is missing', () => {
    expect(() =>
      createNote({ patientName: 'Jane', title: '  ', body: '' }),
    ).toThrow(/title/i)
  })
})

describe('sortNotes', () => {
  it('orders notes by most recently updated first', () => {
    const older: ClinicalNote = {
      id: '1',
      patientName: 'A',
      title: 'Older',
      body: '',
      status: 'draft',
      createdAt: '2024-01-01T00:00:00.000Z',
      updatedAt: '2024-01-01T00:00:00.000Z',
    }
    const newer: ClinicalNote = {
      ...older,
      id: '2',
      title: 'Newer',
      updatedAt: '2024-06-01T00:00:00.000Z',
    }

    const sorted = sortNotes([older, newer])
    expect(sorted.map((n) => n.id)).toEqual(['2', '1'])
  })
})

describe('storage round-trip', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('saves and loads notes', () => {
    const note = createNote({
      patientName: 'John Roe',
      title: 'Intake',
      body: 'Initial assessment.',
    })
    saveNotes([note])

    const loaded = loadNotes()
    expect(loaded).toHaveLength(1)
    expect(loaded[0].title).toBe('Intake')
  })

  it('returns an empty array when nothing is stored', () => {
    expect(loadNotes()).toEqual([])
  })

  it('returns an empty array when stored data is corrupt', () => {
    localStorage.setItem(STORAGE_KEY, 'not-json')
    expect(loadNotes()).toEqual([])
  })
})
