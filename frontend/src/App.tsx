import { type ReactNode, Suspense, lazy } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import { ToastProvider } from './contexts/ToastContext'
import { useAlertsWebSocket } from './hooks/useAlertsWebSocket'
import Layout from './components/Layout/Layout'
import ErrorBoundary from './components/ErrorBoundary/ErrorBoundary'
import PageLoader from './components/PageLoader/PageLoader'

const Login       = lazy(() => import('./pages/Login/Login'))
const Dashboard   = lazy(() => import('./pages/Dashboard/Dashboard'))
const Analyze     = lazy(() => import('./pages/Analyze/Analyze'))
const Reporting   = lazy(() => import('./pages/Reporting/Reporting'))
const History     = lazy(() => import('./pages/History/History'))
const Alerts      = lazy(() => import('./pages/Alerts/Alerts'))
const Reports     = lazy(() => import('./pages/Reports/Reports'))
const SmartCompare = lazy(() => import('./pages/SmartCompare/SmartCompare'))
const Assistant    = lazy(() => import('./pages/Assistant/Assistant'))
const FluxAdmin   = lazy(() => import('./pages/Admin/FluxAdmin'))
const Users       = lazy(() => import('./pages/Admin/Users'))
const Monitoring  = lazy(() => import('./pages/Admin/Monitoring'))
const Profile     = lazy(() => import('./pages/Profile/Profile'))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 60_000,
    },
  },
})

/** Wraps every lazy‑loaded page with an error boundary + suspense fallback */
function LazyRoute({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary>
      <Suspense fallback={<PageLoader />}>
        {children}
      </Suspense>
    </ErrorBoundary>
  )
}

/** Protège les routes privées — redirige vers /login si non connecté */
function PrivateRoute({ children, adminOnly = false }: { children: ReactNode; adminOnly?: boolean }) {
  const { user, isLoading } = useAuth()
  if (isLoading) return <PageLoader />
  if (!user) return <Navigate to="/login" replace />
  if (adminOnly && user.role !== 'admin') return <Navigate to="/" replace />
  return <>{children}</>
}

function AppContent() {
  useAlertsWebSocket()

  return (
    <Routes>
      <Route path="/login" element={<LazyRoute><Login /></LazyRoute>} />

      <Route path="/*" element={
        <PrivateRoute>
          <Layout>
            <Routes>
              <Route path="/" element={<LazyRoute><Dashboard /></LazyRoute>} />
              <Route path="/analyze" element={<LazyRoute><Analyze /></LazyRoute>} />
              <Route path="/reporting" element={<LazyRoute><Reporting /></LazyRoute>} />
              <Route path="/history" element={<LazyRoute><History /></LazyRoute>} />
              <Route path="/alerts" element={<LazyRoute><Alerts /></LazyRoute>} />
              <Route path="/reports" element={<LazyRoute><Reports /></LazyRoute>} />
              <Route path="/smart" element={<LazyRoute><SmartCompare /></LazyRoute>} />
              <Route path="/assistant" element={<LazyRoute><Assistant /></LazyRoute>} />
              <Route path="/profile" element={<LazyRoute><Profile /></LazyRoute>} />
              <Route path="/flux-admin" element={<LazyRoute><PrivateRoute adminOnly><FluxAdmin /></PrivateRoute></LazyRoute>} />
              <Route path="/users" element={<LazyRoute><PrivateRoute adminOnly><Users /></PrivateRoute></LazyRoute>} />
              <Route path="/monitoring" element={<LazyRoute><PrivateRoute adminOnly><Monitoring /></PrivateRoute></LazyRoute>} />
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