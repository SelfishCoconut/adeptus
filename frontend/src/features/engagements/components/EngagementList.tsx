import { Link } from 'react-router-dom'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { useEngagements } from '../api'

export function EngagementList() {
  const { data, isLoading, isError, error } = useEngagements()

  if (isLoading) {
    return (
      <div data-testid="engagement-list-skeleton" className="flex flex-col gap-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    )
  }

  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error instanceof Error ? error.message : 'Failed to load engagements.'}
      </p>
    )
  }

  if (!data || data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No engagements — create one.</p>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {data.map((e) => (
        <Card key={e.id}>
          <CardHeader>
            <CardTitle>{e.name}</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center gap-2">
            <Badge variant="secondary">{e.status}</Badge>
            <Badge variant="outline">{e.member_role}</Badge>
            <Link
              to={`/engagements/${e.id}/workspace`}
              className="ml-auto text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              Open
            </Link>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}
