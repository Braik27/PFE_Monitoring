import { useNavigate } from 'react-router-dom'
import styles from './Topbar.module.css'

interface TopbarProps {
  onRefresh?: () => void
}

export default function Topbar({ onRefresh }: TopbarProps) {
  const navigate = useNavigate()

  return (
    <div className={styles.topbar}>
      <div className={styles.titleSpacer} />
      {onRefresh && (
        <button className="btn bg-btn bsm" onClick={onRefresh}>🔄 Actualiser</button>
      )}
      <button className="btn bp bsm" onClick={() => navigate('/analyze')}>
        + Nouvelle analyse
      </button>
    </div>
  )
}
