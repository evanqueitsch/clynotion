import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App'
import { loadNotes } from './lib/notes'

describe('<App />', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('shows the empty state on first load', () => {
    render(<App />)
    expect(screen.getByText(/no notes yet/i)).toBeInTheDocument()
  })

  it('creates a clinical note and persists it', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByLabelText(/patient name/i), 'Jane Doe')
    await user.type(screen.getByLabelText(/note title/i), 'Follow-up visit')
    await user.type(
      screen.getByLabelText(/^note$/i),
      'Patient reports improvement.',
    )
    await user.click(screen.getByRole('button', { name: /save note/i }))

    expect(screen.getByText('Follow-up visit')).toBeInTheDocument()
    expect(screen.getByText('Jane Doe')).toBeInTheDocument()
    expect(loadNotes()).toHaveLength(1)
  })

  it('validates that a patient name is required', async () => {
    const user = userEvent.setup()
    render(<App />)

    await user.type(screen.getByLabelText(/note title/i), 'Missing patient')
    await user.click(screen.getByRole('button', { name: /save note/i }))

    expect(screen.getByRole('alert')).toHaveTextContent(/patient name/i)
    expect(loadNotes()).toHaveLength(0)
  })
})
