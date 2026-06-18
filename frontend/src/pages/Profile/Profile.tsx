import { useState, useRef, type FormEvent, type ChangeEvent } from 'react'

import { useAuth } from '../../contexts/AuthContext'
import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'

export default function Profile() {
  const { user, refreshUser } = useAuth()
  const { showToast } = useToast()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // ── Profil fields ──────────────────────────────────────────
  const [fullName, setFullName]   = useState(user?.name ?? '')
  const [email,    setEmail]      = useState(user?.email ?? '')
  const [savingProfile, setSavingProfile] = useState(false)

  // ── Avatar ─────────────────────────────────────────────────
  const [avatarPreview, setAvatarPreview] = useState<string | null>(user?.avatar ?? null)
  const [avatarBase64,  setAvatarBase64]  = useState<string | null>(null)

  const handleAvatarClick = () => fileInputRef.current?.click()

  const handleAvatarChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.size > 2 * 1024 * 1024) {
      showToast('Image trop grande (max 2 Mo)', 'error')
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      setAvatarPreview(result)
      setAvatarBase64(result)
    }
    reader.readAsDataURL(file)
  }

  const saveProfile = async (e: FormEvent) => {
    e.preventDefault()
    setSavingProfile(true)
    try {
      const payload: Record<string, string> = { full_name: fullName, email }
      if (avatarBase64) payload.avatar = avatarBase64
      await api.put('/api/profile', payload)
      await refreshUser()
      setAvatarBase64(null)
      showToast('Profil mis à jour', 'success')
    } catch (err: any) {
      showToast(err?.response?.data?.error ?? 'Erreur lors de la mise à jour', 'error')
    } finally {
      setSavingProfile(false)
    }
  }

  // ── Password ───────────────────────────────────────────────
  const [oldPwd,     setOldPwd]     = useState('')
  const [newPwd,     setNewPwd]     = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [savingPwd,  setSavingPwd]  = useState(false)

  const changePwd = async (e: FormEvent) => {
    e.preventDefault()
    if (newPwd !== confirmPwd) return showToast('Les mots de passe ne correspondent pas', 'warning')
    if (newPwd.length < 6)    return showToast('Mot de passe trop court (min. 6 caractères)', 'warning')
    setSavingPwd(true)
    try {
      await api.put('/api/profile/password', { current_password: oldPwd, new_password: newPwd })
      showToast('Mot de passe modifié avec succès', 'success')
      setOldPwd(''); setNewPwd(''); setConfirmPwd('')
    } catch (err: any) {
      showToast(err?.response?.data?.error ?? 'Erreur lors du changement', 'error')
    } finally {
      setSavingPwd(false)
    }
  }

  const ROLE_ICONS: Record<string, string> = { admin: '⚙️', consultant: '🔍', analyst: '📊' }
  const roleIcon  = ROLE_ICONS[user?.role ?? ''] ?? '👤'
  const initials  = (user?.name || user?.username || 'U').charAt(0).toUpperCase()
  const displayAvatar = avatarPreview || user?.avatar

  return (
          <div style={{ maxWidth: 680 }}>

        {/* Mon Profil */}
        <div className="sblk" style={{ marginBottom: 20 }}>
          <div className="sblk-h">
            <span style={{ fontSize: 14, fontWeight: 700 }}>👤 Mon Profil</span>
          </div>

          <form onSubmit={saveProfile} style={{ padding: 24 }}>

            {/* Avatar cliquable */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 20, marginBottom: 28 }}>
              <div
                onClick={handleAvatarClick}
                title="Cliquer pour changer la photo"
                style={{
                  position: 'relative', width: 80, height: 80, borderRadius: '50%',
                  cursor: 'pointer', flexShrink: 0,
                  background: 'linear-gradient(135deg,var(--blue),var(--purple))',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 32, fontWeight: 800, color: '#fff',
                  overflow: 'visible',
                }}
              >
                {displayAvatar
                  ? <img src={displayAvatar} alt="avatar" style={{ width: 80, height: 80, borderRadius: '50%', objectFit: 'cover' }} />
                  : <span>{initials}</span>
                }
                <div style={{
                  position: 'absolute', bottom: 0, right: 0,
                  width: 24, height: 24, borderRadius: '50%',
                  background: 'var(--blue)', border: '2px solid #fff',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 11,
                }}>✏️</div>
              </div>

              <div>
                <div style={{ fontSize: 20, fontWeight: 800 }}>{user?.name || user?.username}</div>
                <div style={{ fontSize: 13, color: 'var(--mut)', fontWeight: 500, marginTop: 2 }}>
                  {roleIcon} {user?.role ? user.role.charAt(0).toUpperCase() + user.role.slice(1) : ''}
                </div>
                <div style={{ fontSize: 11, color: 'var(--mut)', marginTop: 4 }}>
                  Cliquer sur ✏️ pour changer la photo
                </div>
              </div>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={handleAvatarChange}
            />

            <div className="fg">
              <label>NOM COMPLET</label>
              <input
                value={fullName}
                onChange={e => setFullName(e.target.value)}
                placeholder="Votre nom complet"
              />
            </div>

            <div className="fg">
              <label>EMAIL</label>
              <input
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="votre@email.com"
              />
            </div>

            <div className="fg">
              <label>NOM D'UTILISATEUR</label>
              <input
                value={user?.username ?? ''}
                disabled
                style={{ background: 'var(--s2)', color: 'var(--mut)', cursor: 'not-allowed' }}
              />
            </div>

            <button type="submit" className="btn bp" disabled={savingProfile} style={{ marginTop: 4 }}>
              {savingProfile ? <><span className="spin" /> Sauvegarde...</> : '💾 Enregistrer'}
            </button>
          </form>
        </div>

        {/* Changer mot de passe */}
        <div className="sblk">
          <div className="sblk-h">
            <span style={{ fontSize: 14, fontWeight: 700 }}>🔒 Changer le mot de passe</span>
          </div>
          <form onSubmit={changePwd} style={{ padding: 24 }}>
            <div className="fg">
              <label>MOT DE PASSE ACTUEL</label>
              <input
                type="password"
                value={oldPwd}
                onChange={e => setOldPwd(e.target.value)}
                placeholder="••••••••"
              />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              <div className="fg">
                <label>NOUVEAU MOT DE PASSE</label>
                <input
                  type="password"
                  value={newPwd}
                  onChange={e => setNewPwd(e.target.value)}
                  placeholder="Min. 6 caractères"
                />
              </div>
              <div className="fg">
                <label>CONFIRMER LE NOUVEAU</label>
                <input
                  type="password"
                  value={confirmPwd}
                  onChange={e => setConfirmPwd(e.target.value)}
                  placeholder="••••••••"
                />
              </div>
            </div>
            <button type="submit" className="btn bp" disabled={savingPwd}>
              {savingPwd ? <><span className="spin" /> Sauvegarde...</> : '🔒 Changer le mot de passe'}
            </button>
          </form>
        </div>

      </div>
  )
}