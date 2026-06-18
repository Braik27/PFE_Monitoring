import { useState, useEffect } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Bell,
  Bot,
  Brain,
  Database,
  History,
  LayoutDashboard,
  LogOut,
  Search,
  TrendingUp,
  Users,
} from 'lucide-react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../../contexts/AuthContext'
import styles from './Sidebar.module.css'
import api from '../../lib/api'
import { useQuery } from '@tanstack/react-query'

interface NavItem {
  id: string
  path: string
  icon: LucideIcon
  label: string
  adminOnly?: boolean
  badgeId?: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', path: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { id: 'analyze', path: '/analyze', icon: Search, label: 'Analyser' },
  { id: 'reporting', path: '/reporting', icon: TrendingUp, label: 'Reporting' },
  { id: 'history', path: '/history', icon: History, label: 'Historique' },
  { id: 'alerts', path: '/alerts', icon: Bell, label: 'Alertes', badgeId: 'alertBadge' },
]

const IA_ITEMS: NavItem[] = [
  { id: 'smart', path: '/smart', icon: Brain, label: 'Analyse IA' },
  { id: 'assistant', path: '/assistant', icon: Bot, label: 'Assistant IA' },
]

const ADMIN_ITEMS: NavItem[] = [
  { id: 'flux-admin', path: '/flux-admin', icon: Database, label: 'Gestion des flux', adminOnly: true },
  { id: 'users', path: '/users', icon: Users, label: 'Utilisateurs', adminOnly: true },
]

const ROLE_COLORS: Record<string, string> = {
  admin: '#fbbf24',
  consultant: '#34d399',
  analyst: '#60a5fa',
}

export default function Sidebar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [alertCount, setAlertCount] = useState(0)

  const isAdmin = user?.role === 'admin'

  const { data: alertsData } = useQuery({
    queryKey: ['sidebar-alerts'],
    queryFn: async () => {
      const r = await api.get('/api/alerts?limit=200')
      return r.data
    },
    staleTime: 120_000,
  })

  useEffect(() => {
    if (alertsData) {
      const arr = Array.isArray(alertsData) ? alertsData : []
      const pending = arr.filter((a: { status?: string }) =>
        ['NEW', 'PENDING', 'ACKNOWLEDGED'].includes(String(a.status ?? '').toUpperCase()),
      )
      setAlertCount(pending.length)
    }
  }, [alertsData])

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/'
    return location.pathname.startsWith(path)
  }

  const renderItem = (item: NavItem) => {
    const Icon = item.icon

    return (
      <div
        key={item.id}
        className={`${styles.item} ${isActive(item.path) ? styles.active : ''}`}
        onClick={() => navigate(item.path)}
      >
        <span className={styles.iconWrap}>
          <Icon className={styles.icon} />
        </span>
        <span className={styles.label}>{item.label}</span>
        {item.badgeId && alertCount > 0 && (
          <span className={styles.badge}>{alertCount}</span>
        )}
      </div>
    )
  }

  const initials = (user?.name || user?.username || 'U').charAt(0).toUpperCase()

  return (
    <aside className={styles.sidebar}>
      {/* Logo */}
      <div className={styles.logo}>
        <img
          src="/static/logo-timsofta.png"
          alt="TimSoft"
          style={{ width: 36, height: 36, objectFit: 'contain' }}
          onError={(e) => { (e.target as HTMLImageElement).style.opacity = '0' }}
        />
        <div>
          <div className={styles.name}>
            <span style={{ fontStyle: 'italic' }}>tim</span>soft
            <span className={styles.dot} />
          </div>
        </div>
      </div>

      {/* Navigation */}
      <nav className={styles.nav}>
        <div className={styles.group}>Suivi</div>
        {NAV_ITEMS.map(renderItem)}

        <div className={styles.group}>IA</div>
        {IA_ITEMS.map(renderItem)}

        {isAdmin && (
          <>
            <div className={styles.group}>Admin</div>
            {ADMIN_ITEMS.map(renderItem)}
          </>
        )}
      </nav>

      {/* User card */}
      <div className={styles.userCard} onClick={() => navigate('/profile')} style={{ cursor: 'pointer' }}>
        <div className={styles.userTop}>
          <div className={styles.avatar} style={{ overflow: 'hidden', padding: 0 }}>
            {user?.avatar
              ? <img src={user.avatar} alt="avatar" style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '50%' }} />
              : initials
            }
          </div>
          <div className={styles.userInfo}>
            <div className={styles.userName}>{user?.name || user?.username || '—'}</div>
            <div className={styles.userRole} style={{ color: ROLE_COLORS[user?.role ?? ''] ?? '#94a3b8' }}>
              {user?.role ?? '—'}
            </div>
          </div>
        </div>
        <div className={styles.userStats}>
          <div className={styles.stat}>
            <div className={styles.statVal}>{user?.n_analyses ?? 0}</div>
            <div className={styles.statLbl}>Analyses</div>
          </div>
          <div className={styles.stat}>
            <div className={styles.statVal}>{alertCount}</div>
            <div className={styles.statLbl}>Alertes</div>
          </div>
        </div>
        <button
          className={styles.logoutBtn}
          onClick={(e) => { e.stopPropagation(); logout() }}
        >
          <LogOut size={15} className={styles.logoutIcon} />
          <span>Déconnexion</span>
        </button>
      </div>
    </aside>
  )
}