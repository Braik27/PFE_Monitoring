import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

interface Toast {
  id: number
  message: string
  type: 'success' | 'error' | 'info' | 'warning'
  icon?: string
  onClick?: () => void
}

interface ToastContextType {
  toasts: Toast[]
  showToast: (message: string, type?: Toast['type'], icon?: string, onClick?: () => void) => void
}

const ToastContext = createContext<ToastContextType | null>(null)

let idCounter = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const showToast = useCallback((message: string, type: Toast['type'] = 'info', icon?: string, onClick?: () => void) => {
    const id = ++idCounter
    setToasts((prev) => [...prev, { id, message, type, icon, onClick }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4000)
  }, [])

  return (
    <ToastContext.Provider value={{ toasts, showToast }}>
      {children}
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
