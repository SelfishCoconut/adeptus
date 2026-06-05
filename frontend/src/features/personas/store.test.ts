import { beforeEach, describe, expect, it } from 'vitest'
import { usePersonaSelectionStore } from './store'

const ENG = 'eng-1'
const GENERAL = 'general-id'

beforeEach(() => {
  usePersonaSelectionStore.setState({ selectedByEngagement: {} })
})

describe('usePersonaSelectionStore', () => {
  it('defaults to general when nothing is selected', () => {
    expect(usePersonaSelectionStore.getState().selectedPersonaId(ENG, GENERAL)).toBe(GENERAL)
  })

  it('select updates the selection for that engagement', () => {
    usePersonaSelectionStore.getState().select(ENG, 'recon-id')
    expect(usePersonaSelectionStore.getState().selectedPersonaId(ENG, GENERAL)).toBe('recon-id')
  })

  it('keeps selections isolated per engagement', () => {
    usePersonaSelectionStore.getState().select(ENG, 'recon-id')
    expect(usePersonaSelectionStore.getState().selectedPersonaId('eng-2', GENERAL)).toBe(GENERAL)
  })

  it('reconcile falls back to general when the selected persona no longer exists', () => {
    usePersonaSelectionStore.getState().select(ENG, 'custom-id')
    usePersonaSelectionStore.getState().reconcile(ENG, [GENERAL, 'recon-id'])
    expect(usePersonaSelectionStore.getState().selectedPersonaId(ENG, GENERAL)).toBe(GENERAL)
  })

  it('reconcile keeps a still-valid selection', () => {
    usePersonaSelectionStore.getState().select(ENG, 'recon-id')
    usePersonaSelectionStore.getState().reconcile(ENG, [GENERAL, 'recon-id'])
    expect(usePersonaSelectionStore.getState().selectedPersonaId(ENG, GENERAL)).toBe('recon-id')
  })
})
