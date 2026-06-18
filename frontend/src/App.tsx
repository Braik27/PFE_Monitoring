import type { ReactNode } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ToastProvider } from './contexts/ToastContext'
import { useAlertsWebSocket } from './hooks/useAlertsWebSocket'
import Layout from './components/Layout/Layout'

// Pages
import Login       from './pages/Login/Login'
import Dashboard   from './pages/Dashboard/Dashboard'
import Analyze     from './pages/Analyze/Analyze'
import Reporting   from './pages/Reporting/Reporting'
import History     from './pages/History/History'
import Alerts      from './pages/Alerts/Alerts'
import Reports     from './pages/Reports/Reports'
import SmartCompare from './pages/SmartCompare/SmartCompare'
import Assistant    from './pages/Assistant/Assistant'
import FluxAdmin   from './pages/Admin/FluxAdmin'
import Users       from './pages/Admin/Users'
import Monitoring  from './pages/Admin/Monitoring'
import Profile     from './pages/Profile/Profile'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 60_000,
    },
  },
})

/** Protège les routes privées — redirige vers /login si non connecté */
function PrivateRoute({ children, adminOnly = false }: { children: ReactNode; adminOnly?: boolean }) {
  const { user, isLoading } = useAuth()
  if (isLoading) return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', color: 'var(--mut)' }}>Chargement...</div>
  if (!user) return <Navigate to="/login" replace />
  if (adminOnly && user.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

function AppContent() {
  useAlertsWebSocket()

  return (
    <Routes>
      <Route path="/login" element={<Login />} />

      <Route path="/*" element={
        <PrivateRoute>
          <Layout>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/analyze" element={<Analyze />} />
              <Route path="/reporting" element={<Reporting />} />
              <Route path="/history" element={<History />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="/reports" element={<Reports />} />
              <Route path="/smart" element={<SmartCompare />} />
              <Route path="/assistant" element={<Assistant />} />
              <Route path="/profile" element={<Profile />} />
              <Route path="/flux-admin" element={<PrivateRoute adminOnly><FluxAdmin /></PrivateRoute>} />
              <Route path="/users" element={<PrivateRoute adminOnly><Users /></PrivateRoute>} />
              <Route path="/monitoring" element={<PrivateRoute adminOnly><Monitoring /></PrivateRoute>} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Layout>
        </PrivateRoute>
      } />
    </Routes>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToastProvider>
          <BrowserRouter>
            <AppContent />
          </BrowserRouter>
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>
  )
}