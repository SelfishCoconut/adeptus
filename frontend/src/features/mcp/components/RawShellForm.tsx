import { useState, type FormEvent, type ChangeEvent } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useEngagements } from '@/features/engagements/api'
import { useExecuteToolRun } from '../api'
import type { ToolRunResult } from '@/shared/api'

const TRUNCATION_SENTINEL = '[output truncated at 1 MB]'
const DEFAULT_TIMEOUT = 30
const MIN_TIMEOUT = 1
const MAX_TIMEOUT = 300

function hasTruncation(text: string): boolean {
  return text.includes(TRUNCATION_SENTINEL)
}

interface RawShellFormProps {
  /** When provided, the engagement selector is initialised to this value. */
  initialEngagementId?: string
}

export function RawShellForm({ initialEngagementId }: RawShellFormProps = {}) {
  const engagements = useEngagements()
  const executeToolRun = useExecuteToolRun()

  const [command, setCommand] = useState('')
  const [timeoutSeconds, setTimeoutSeconds] = useState<number>(DEFAULT_TIMEOUT)
  const [engagementId, setEngagementId] = useState<string>(initialEngagementId ?? '')
  const [result, setResult] = useState<ToolRunResult | null>(null)

  // Initialise engagementId to the first engagement once loaded (only when
  // no initialEngagementId was provided and user has not made a selection yet).
  const engagementList = engagements.data ?? []
  const effectiveEngagementId =
    engagementId || (engagementList.length > 0 ? engagementList[0].id : '')

  function handleTimeoutChange(e: ChangeEvent<HTMLInputElement>) {
    const raw = parseInt(e.target.value, 10)
    if (isNaN(raw)) {
      setTimeoutSeconds(DEFAULT_TIMEOUT)
    } else {
      setTimeoutSeconds(Math.min(MAX_TIMEOUT, Math.max(MIN_TIMEOUT, raw)))
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!effectiveEngagementId) return

    setResult(null)
    executeToolRun.mutate(
      {
        engagement_id: effectiveEngagementId,
        server_name: 'shell-exec',
        tool_name: 'run_command',
        args: { command },
        timeout_seconds: timeoutSeconds,
        async_mode: false,
      },
      {
        onSuccess: (data) => {
          setResult(data)
        },
      },
    )
  }

  const isTruncated =
    result !== null && (hasTruncation(result.stdout) || hasTruncation(result.stderr))

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-base font-semibold">Raw Shell</h2>

      <form onSubmit={handleSubmit} className="flex flex-col gap-4" noValidate>
        {/* Engagement selector */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="raw-shell-engagement">Engagement</Label>
          {engagements.isLoading ? (
            <p className="text-sm text-muted-foreground">Loading engagements…</p>
          ) : engagements.isError ? (
            <p role="alert" className="text-sm text-destructive">
              Failed to load engagements.
            </p>
          ) : (
            <select
              id="raw-shell-engagement"
              value={effectiveEngagementId}
              onChange={(e) => setEngagementId(e.target.value)}
              disabled={executeToolRun.isPending}
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            >
              {engagementList.length === 0 ? (
                <option value="">No engagements available</option>
              ) : (
                engagementList.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.name}
                  </option>
                ))
              )}
            </select>
          )}
        </div>

        {/* Command input */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="raw-shell-command">Command</Label>
          <Input
            id="raw-shell-command"
            name="command"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder="echo hello"
            required
            disabled={executeToolRun.isPending}
          />
        </div>

        {/* Timeout input */}
        <div className="flex flex-col gap-2">
          <Label htmlFor="raw-shell-timeout">Timeout (seconds)</Label>
          <Input
            id="raw-shell-timeout"
            name="timeout_seconds"
            type="number"
            min={MIN_TIMEOUT}
            max={MAX_TIMEOUT}
            value={timeoutSeconds}
            onChange={handleTimeoutChange}
            disabled={executeToolRun.isPending}
          />
        </div>

        {/* Submit */}
        <div>
          <Button type="submit" disabled={executeToolRun.isPending || !effectiveEngagementId}>
            {executeToolRun.isPending ? 'Running…' : 'Run'}
          </Button>
        </div>
      </form>

      {/* Error state */}
      {executeToolRun.isError && (
        <p role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {executeToolRun.error instanceof Error
            ? executeToolRun.error.message
            : 'Failed to execute tool run.'}
        </p>
      )}

      {/* Truncation notice */}
      {isTruncated && (
        <p
          role="status"
          className="rounded-md border border-yellow-400/40 bg-yellow-50 px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-300"
        >
          Output was truncated at 1 MB.
        </p>
      )}

      {/* Results */}
      {result !== null && (
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium">
            Exit code:{' '}
            <span
              className={result.exit_code === 0 ? 'text-green-600' : 'text-destructive'}
            >
              {result.exit_code}
            </span>
          </p>

          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-muted-foreground">stdout</p>
            <pre className="overflow-x-auto rounded-md border bg-muted p-3 text-xs">
              {result.stdout || '(empty)'}
            </pre>
          </div>

          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-muted-foreground">stderr</p>
            <pre className="overflow-x-auto rounded-md border bg-muted p-3 text-xs">
              {result.stderr || '(empty)'}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
