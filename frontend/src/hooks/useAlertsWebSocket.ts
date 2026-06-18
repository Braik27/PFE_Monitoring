import { useEffect, useRef, useCallback } from 'react'
import { useToast } from '../contexts/ToastContext'
import { useAuth } from '../contexts/AuthContext'

const WS_URL = (): string => {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/alerts`
}

export function useAlertsWebSocket() {
  const { showToast } = useToast()
  const { user, isLoading } = useAuth()
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number | null>(null)

  const connect = useCallback(() => {
    if (!user || isLoading) return

    // Clean up existing connection
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }

    try {
      const ws = new WebSocket(WS_URL())

      ws.onopen = () => {
        console.log('[WS] Connected to alerts channel')
      }

      ws.onmessage = (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type === 'new_alert') {
            const fluxName = data.flux_name ?? 'Inconnu'
            const token = data.token ?? ''
            const nCritiques = data.n_critiques ?? 0

            const severity = nCritiques > 0 ? 'error' : 'warning'
            const icon = nCritiques > 0 ? '🚨' : '⚠️'
            showToast(
              `${icon} Nouvelle alerte sur ${fluxName}${nCritiques > 0 ? ` (${nCritiques} critique${nCritiques > 1 ? 's' : ''})` : ''} — Token: ${token.slice(0, 8)}…`,
              severity as any,
              icon
            )

            // Dispatch custom event so other components can react
            window.dispatchEvent(new CustomEvent('new-alert', { detail: data }))
          }
        } catch {
          // Ignore malformed messages
        }
      }

      ws.onclose = (event: CloseEvent) => {
        console.log(`[WS] Disconnected (code: ${event.code}, reason: ${event.reason || 'none'})`)
        wsRef.current = null

        // Auto-reconnect after 5 seconds (only if the component is still mounted and user is logged in)
        if (user) {
          reconnectTimeoutRef.current = window.setTimeout(() => {
            console.log('[WS] Reconnecting...')
            connect()
          }, 5000)
        }
      }

      ws.onerror = (error: Event) => {
        console.error('[WS] Error:', error)
      }

      wsRef.current = ws
    } catch (err) {
      console.error('[WS] Failed to connect:', err)
    }
  }, [user, isLoading, showToast])

  const disconnect = useCallback(() => {
    if (reconnectTimeoutRef.current != null) {
      clearTimeout(reconnectTimeoutRef.current)
      reconnectTimeoutRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
  }, [])

  useEffect(() => {
    if (user && !isLoading) {
      connect()
    } else {
      disconnect()
    }

    return () => {
      disconnect()
    }
  }, [user, isLoading, connect, disconnect])

  return { connected: !!wsRef.current }
}