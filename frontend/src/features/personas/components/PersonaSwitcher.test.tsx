import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Persona } from '@/shared/api'
import { PersonaSwitcher } from './PersonaSwitcher'

const persona = (id: string, name: string, isBuiltin: boolean, slug: string | null): Persona => ({
  id,
  name,
  system_prompt: `${name} prompt`,
  is_builtin: isBuiltin,
  slug,
  created_at: '2026-01-01T00:00:00Z',
})

const PERSONAS: Persona[] = [
  persona('general-id', 'General', true, 'general'),
  persona('recon-id', 'Recon', true, 'recon'),
  persona('custom-id', 'Cloud Pentest', false, null),
]

describe('PersonaSwitcher', () => {
  it('renders built-ins and the callers custom personas as options', () => {
    render(
      <PersonaSwitcher
        personas={PERSONAS}
        selectedId="general-id"
        onChange={vi.fn()}
        onManage={vi.fn()}
      />,
    )
    expect(screen.getByRole('option', { name: 'General' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Recon' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Cloud Pentest' })).toBeInTheDocument()
  })

  it('groups built-ins under a "Built-in" affordance separate from custom personas', () => {
    const { container } = render(
      <PersonaSwitcher
        personas={PERSONAS}
        selectedId="general-id"
        onChange={vi.fn()}
        onManage={vi.fn()}
      />,
    )
    expect(container.querySelector('optgroup[label="Built-in"]')).not.toBeNull()
    expect(container.querySelector('optgroup[label="Your personas"]')).not.toBeNull()
  })

  it('reflects the selected persona', () => {
    render(
      <PersonaSwitcher
        personas={PERSONAS}
        selectedId="recon-id"
        onChange={vi.fn()}
        onManage={vi.fn()}
      />,
    )
    expect(screen.getByRole('combobox')).toHaveValue('recon-id')
  })

  it('calls onChange with the chosen persona id', async () => {
    const onChange = vi.fn()
    render(
      <PersonaSwitcher
        personas={PERSONAS}
        selectedId="general-id"
        onChange={onChange}
        onManage={vi.fn()}
      />,
    )
    await userEvent.selectOptions(screen.getByRole('combobox'), 'custom-id')
    expect(onChange).toHaveBeenCalledWith('custom-id')
  })

  it('calls onManage when the manage affordance is clicked', async () => {
    const onManage = vi.fn()
    render(
      <PersonaSwitcher
        personas={PERSONAS}
        selectedId="general-id"
        onChange={vi.fn()}
        onManage={onManage}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /manage personas/i }))
    expect(onManage).toHaveBeenCalledOnce()
  })

  it('omits the custom group when the caller has no custom personas', () => {
    const { container } = render(
      <PersonaSwitcher
        personas={PERSONAS.filter((p) => p.is_builtin)}
        selectedId="general-id"
        onChange={vi.fn()}
        onManage={vi.fn()}
      />,
    )
    expect(container.querySelector('optgroup[label="Your personas"]')).toBeNull()
  })
})
