import type { ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { useAcceptTerms, useMe } from '../api'

const TERMS_TEXT =
  'By using Adeptus you agree to use it only on systems you have explicit written permission to test.'

export function TermsGate({ children }: { children: ReactNode }) {
  const me = useMe()
  const acceptTerms = useAcceptTerms()

  if (me.data && me.data.terms_accepted_at === null) {
    return (
      <div className="flex min-h-svh items-center justify-center bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Terms of use</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">{TERMS_TEXT}</p>
            {acceptTerms.error && (
              <p role="alert" className="mt-2 text-sm text-destructive">
                {acceptTerms.error.message}
              </p>
            )}
          </CardContent>
          <CardFooter>
            <Button onClick={() => acceptTerms.mutate()} disabled={acceptTerms.isPending}>
              {acceptTerms.isPending ? 'Accepting…' : 'Accept'}
            </Button>
          </CardFooter>
        </Card>
      </div>
    )
  }

  return <>{children}</>
}
