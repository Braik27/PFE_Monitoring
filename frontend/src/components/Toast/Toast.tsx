import { useToast } from '../../contexts/ToastContext'
import styles from './Toast.module.css'

const ICONS: Record<string, string> = {
  success: '✅',
  error: '❌',
  warning: '⚠️',
  info: 'ℹ️',
}

const COLORS: Record<string, string> = {
  success: 'var(--green)',
  error: 'var(--red)',
  warning: 'var(--orange)',
  info: 'var(--blue)',
}

export default function ToastContainer() {
  const { toasts } = useToast()
  return (
    <div className={styles.container}>
      {toasts.map((t) => (
        <div key={t.id} className={styles.toast} style={{ borderLeftColor: COLORS[t.type] }}>
          <span style={{ fontSize: 18 }}>{t.icon ?? ICONS[t.type]}</span>
          <span>{t.message}</span>
        </div>
      ))}
    </div>
  )
}
