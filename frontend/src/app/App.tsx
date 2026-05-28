import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { ProtectedRoute } from './ProtectedRoute'
import { LoginPage } from './LoginPage'
import { WorkspacePage } from './WorkspacePage'

const queryClient = new QueryClient()

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/workspace"
            element={
              <ProtectedRoute>
                <WorkspacePage />
              </ProtectedRoute>
            }
          />
          <Route path="/" element={<Navigate to="/workspace" replace />} />
          <Route path="*" element={<Navigate to="/workspace" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
