import type { Persona } from '@/shared/api'

interface PersonaSwitcherProps {
  /** The personas available to the caller (built-ins first, then customs) — from usePersonas. */
  personas: Persona[]
  /** The currently-selected persona id (from the selection store, defaulting to general). */
  selectedId: string
  /** Called with the chosen persona id when the user switches persona. */
  onChange: (personaId: string) => void
  /** Open the "Manage personas" panel (create/edit/delete custom personas). */
  onManage: () => void
  /** Disable the switcher (e.g. while a turn is streaming or the engagement is archived). */
  disabled?: boolean
}

/**
 * The composer's persona switcher (§5.3): a small native select grouping the read-only
 * built-ins above the caller's own custom personas, plus a "Manage personas" affordance.
 * Purely presentational — the selection lives in the ephemeral store (task 14) and is read
 * per send (task 17), so switching takes effect on the next turn with no conversation reset.
 */
export function PersonaSwitcher({
  personas,
  selectedId,
  onChange,
  onManage,
  disabled = false,
}: PersonaSwitcherProps) {
  const builtins = personas.filter((p) => p.is_builtin)
  const custom = personas.filter((p) => !p.is_builtin)

  return (
    <div className="flex items-center gap-1">
      <select
        aria-label="Persona"
        value={selectedId}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="h-8 rounded-md border border-input bg-background px-2 text-xs text-foreground disabled:cursor-not-allowed disabled:opacity-50"
      >
        <optgroup label="Built-in">
          {builtins.map((persona) => (
            <option key={persona.id} value={persona.id}>
              {persona.name}
            </option>
          ))}
        </optgroup>
        {custom.length > 0 ? (
          <optgroup label="Your personas">
            {custom.map((persona) => (
              <option key={persona.id} value={persona.id}>
                {persona.name}
              </option>
            ))}
          </optgroup>
        ) : null}
      </select>
      <button
        type="button"
        onClick={onManage}
        className="px-1 text-xs text-muted-foreground hover:text-foreground"
      >
        Manage personas
      </button>
    </div>
  )
}
