import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ThemeProvider } from '@/components/theme/ThemeProvider'
import { ProtectedRoute } from './ProtectedRoute'
import { AdminRoute } from './AdminRoute'
import { LoginPage } from './LoginPage'
import { WorkspacePage } from './WorkspacePage'
import { EngagementsPage } from '@/features/engagements/pages/EngagementsPage'
import { EngagementWorkspacePage } from '@/features/engagements/pages/EngagementWorkspacePage'
import { McpServersPage } from '@/features/admin/pages/McpServersPage'

const queryClient = new QueryClient()

export function App() {
  return (
    <ThemeProvider defaultTheme="system">
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route
              path="/engagements"
              element={
                <ProtectedRoute>
                  <EngagementsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/engagements/:id/workspace"
              element={
                <ProtectedRoute>
                  <EngagementWorkspacePage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/admin/mcp-servers"
              element={
                <AdminRoute>
                  <McpServersPage />
                </AdminRoute>
              }
            />
            {/* Legacy route: redirect to /engagements. Kept so any bookmarks or
                Slice 00 navigation to /workspace doesn't hard-404. */}
            <Route
              path="/workspace"
              element={
                <ProtectedRoute>
                  <WorkspacePage />
                </ProtectedRoute>
              }
            />
            <Route path="/" element={<Navigate to="/engagements" replace />} />
            <Route path="*" element={<Navigate to="/engagements" replace />} />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
