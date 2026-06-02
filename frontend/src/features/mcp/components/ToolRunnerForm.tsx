import { useState, type FormEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useExecuteToolRunAsync, useListTools } from '../api'
import type { ToolDescriptor } from '@/shared/api'

const SANDBOX_TARGET = 'http://localhost:3000'
const DEFAULT_TIMEOUT = 30

const SELECT_CLASS =
  'flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50'

type FieldType = 'string' | 'integer' | 'array'

interface ArgField {
  name: string
  type: FieldType
  description?: string
}

/** Derive the flat list of renderable arg fields from a tool's JSON arg_schema. */
function parseArgSchema(schema: Record<string, unknown>): ArgField[] {
  const properties = schema.properties
  if (typeof properties !== 'object' || properties === null) return []

  const fields: ArgField[] = []
  for (const [name, raw] of Object.entries(properties as Record<string, unknown>)) {
    if (typeof raw !== 'object' || raw === null) continue
    const prop = raw as Record<string, unknown>
    const description = typeof prop.description === 'string' ? prop.description : undefined
    if (prop.type === 'array') {
      fields.push({ name, type: 'array', description })
    } else if (prop.type === 'integer' || prop.type === 'number') {
      fields.push({ name, type: 'integer', description })
    } else {
      fields.push({ name, type: 'string', description })
    }
  }
  return fields
}

/** Initial display values: target pre-fills with the sandbox URL, the rest blank. */
function initialValues(fields: ArgField[]): Record<string, string> {
  const values: Record<string, string> = {}
  for (const f of fields) {
    if (f.name === 'target' && f.type === 'string') {
      values[f.name] = SANDBOX_TARGET
    } else if (f.name === 'timeout_seconds' && f.type === 'integer') {
      values[f.name] = String(DEFAULT_TIMEOUT)
    } else {
      values[f.name] = ''
    }
  }
  return values
}

/** Stringify a preset value so it can populate a text input. */
function presetValueToString(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(' ')
  if (value === null || value === undefined) return ''
  return String(value)
}

/** Convert the display values into the typed args object for the request. */
function buildArgs(fields: ArgField[], values: Record<string, string>): Record<string, unknown> {
  const args: Record<string, unknown> = {}
  for (const f of fields) {
    const raw = values[f.name] ?? ''
    if (f.type === 'integer') {
      const n = parseInt(raw, 10)
      if (!Number.isNaN(n)) args[f.name] = n
    } else if (f.type === 'array') {
      args[f.name] = raw.split(/\s+/).filter(Boolean)
    } else {
      args[f.name] = raw
    }
  }
  return args
}

interface ToolRunnerFormProps {
  engagementId: string
  /** Called with the new run id once the async run has been accepted (202). */
  onRunStarted: (toolRunId: string) => void
}

export function ToolRunnerForm({ engagementId, onRunStarted }: ToolRunnerFormProps) {
  const tools = useListTools()
  const executeAsync = useExecuteToolRunAsync()

  const [selectedKey, setSelectedKey] = useState('')
  const [presetName, setPresetName] = useState('')
  const [values, setValues] = useState<Record<string, string>>({})

  const descriptors = tools.data ?? []
  const selected = descriptors.find((d) => `${d.server_name}/${d.tool_name}` === selectedKey)
  const fields = selected ? parseArgSchema(selected.arg_schema) : []

  // Reset preset + arg values when the selected tool changes (adjust state
  // during render rather than in an effect).
  const [trackedKey, setTrackedKey] = useState(selectedKey)
  if (trackedKey !== selectedKey) {
    setTrackedKey(selectedKey)
    setPresetName('')
    setValues(initialValues(fields))
  }

  // Group descriptors by server name for the <optgroup>s.
  const byServer = new Map<string, ToolDescriptor[]>()
  for (const d of descriptors) {
    const list = byServer.get(d.server_name) ?? []
    list.push(d)
    byServer.set(d.server_name, list)
  }

  function handlePresetChange(name: string) {
    setPresetName(name)
    const preset = selected?.presets.find((p) => p.name === name)
    if (!preset) return
    setValues((prev) => {
      const next = { ...prev }
      for (const [key, val] of Object.entries(preset.args)) {
        next[key] = presetValueToString(val)
      }
      return next
    })
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selected || !engagementId) return

    const args = buildArgs(fields, values)
    const timeoutArg = args.timeout_seconds
    const timeout_seconds = typeof timeoutArg === 'number' ? timeoutArg : DEFAULT_TIMEOUT

    executeAsync.mutate(
      {
        engagement_id: engagementId,
        server_name: selected.server_name,
        tool_name: selected.tool_name,
        args,
        timeout_seconds,
        async_mode: true,
        preset_name: presetName || null,
      },
      {
        onSuccess: (data) => onRunStarted(data.tool_run_id),
      },
    )
  }

  const showSandboxNotice = import.meta.env.DEV && fields.some((f) => f.name === 'target')

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Tool Runner</h2>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {/* Tool selector */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="tool-runner-tool">Tool</Label>
          {tools.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading tools…</p>
          ) : tools.isError ? (
            <p role="alert" className="text-sm text-destructive">
              Failed to load tools.
            </p>
          ) : (
            <select
              id="tool-runner-tool"
              value={selectedKey}
              onChange={(e) => setSelectedKey(e.target.value)}
              disabled={executeAsync.isPending}
              className={SELECT_CLASS}
            >
              <option value="">Select a tool…</option>
              {[...byServer.entries()].map(([server, list]) => (
                <optgroup key={server} label={server}>
                  {list.map((d) => (
                    <option key={`${d.server_name}/${d.tool_name}`} value={`${d.server_name}/${d.tool_name}`}>
                      {d.tool_name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          )}
        </div>

        {selected && (
          <>
            {/* Preset selector */}
            <div className="flex flex-col gap-2">
              <Label htmlFor="tool-runner-preset">Preset</Label>
              <select
                id="tool-runner-preset"
                value={presetName}
                onChange={(e) => handlePresetChange(e.target.value)}
                disabled={executeAsync.isPending}
                className={SELECT_CLASS}
              >
                <option value="">Custom (manual args)</option>
                {selected.presets.map((p) => (
                  <option key={p.name} value={p.name}>
                    {p.name}
                    {p.description ? ` — ${p.description}` : ''}
                  </option>
                ))}
              </select>
            </div>

            {/* Dynamic arg fields */}
            {fields.map((field) => (
              <div key={field.name} className="flex flex-col gap-2">
                <Label htmlFor={`tool-runner-arg-${field.name}`}>{field.name}</Label>
                <Input
                  id={`tool-runner-arg-${field.name}`}
                  name={field.name}
                  type={field.type === 'integer' ? 'number' : 'text'}
                  value={values[field.name] ?? ''}
                  onChange={(e) =>
                    setValues((prev) => ({ ...prev, [field.name]: e.target.value }))
                  }
                  placeholder={field.type === 'array' ? 'space-separated values' : undefined}
                  disabled={executeAsync.isPending}
                />
                {field.description && (
                  <p className="text-xs text-muted-foreground">{field.description}</p>
                )}
              </div>
            ))}

            {/* Sandbox guard notice */}
            {showSandboxNotice && (
              <p
                role="status"
                className="rounded-md border border-yellow-400/40 bg-yellow-50 px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-300"
              >
                Dev mode: tools may only target the sandbox ({SANDBOX_TARGET}). Other targets are
                rejected by the server.
              </p>
            )}

            {/* Submit */}
            <div>
              <Button type="submit" disabled={executeAsync.isPending || !engagementId}>
                {executeAsync.isPending ? 'Running…' : 'Run'}
              </Button>
            </div>
          </>
        )}
      </form>

      {/* Error state */}
      {executeAsync.isError && (
        <p
          role="alert"
          className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {executeAsync.error instanceof Error
            ? executeAsync.error.message
            : 'Failed to execute tool run.'}
        </p>
      )}
    </div>
  )
}
