import type { ReactNode } from 'react'
import Sidebar from '../Sidebar/Sidebar'
import Topbar from '../Topbar/Topbar'
import ToastContainer from '../Toast/Toast'
import styles from './Layout.module.css'

interface LayoutProps {
  children: ReactNode
  onRefresh?: () => void
}

export default function Layout({ children, onRefresh }: LayoutProps) {
  return (
    <div className={styles.app}>
      <Sidebar />
      <main className={styles.main}>
        <Topbar onRefresh={onRefresh} />
        <div className={styles.content}>
          {children}
        </div>
      </main>
      <ToastContainer />
    </div>
  )
}
