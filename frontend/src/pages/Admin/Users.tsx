import { useEffect, useState, useCallback } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'

export default function Users() {
  const { showToast } = useToast()
  const [users, setUsers] = useState<any[]>([])
  const [modal, setModal] = useState(false)
  const [form, setForm] = useState({ username: '', password: '', name: '', email: '', role: 'analyst' })

  const load = useCallback(async () => {
    try {
      const res = await api.get('/api/admin/users')
      setUsers(res.data.users ?? res.data ?? [])
    } catch { showToast('Erreur chargement utilisateurs', 'error') }
  }, [showToast])

  useEffect(() => { load() }, [load])

  const save = async () => {
    try {
      await api.post('/api/admin/users', {
        username: form.username,
        password: form.password,
        full_name: form.name,
        email: form.email,
        role: form.role,
      })
      showToast('Utilisateur créé', 'success')
      setModal(false)
      setForm({ username: '', password: '', name: '', email: '', role: 'analyst' })
      load()
    } catch (e: any) { showToast(e?.response?.data?.error ?? 'Erreur', 'error') }
  }

  const del = async (u: { id: number; username: string }) => {
    if (!confirm(`Supprimer l'utilisateur ${u.username} ?`)) return
    try { await api.delete(`/api/admin/users/${u.id}`); showToast('Utilisateur supprimé', 'success'); load() }
    catch { showToast('Erreur suppression', 'error') }
  }

  const ROLE_COLORS: Record<string, string> = { admin: '#fbbf24', consultant: '#34d399', analyst: '#60a5fa' }

  return (
    <>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 16 }}>
        <button className="btn bp" onClick={() => setModal(true)}>+ Nouvel utilisateur</button>
      </div>

      <div className="sblk">
        <div className="sblk-h">
          <span style={{ fontSize: 13, fontWeight: 700 }}>👥 Gestion des utilisateurs</span>
          <span style={{ fontSize: 11, color: 'var(--mut)' }}>Seul l'admin peut créer des comptes</span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr><th>#</th><th>Utilisateur</th><th>Nom</th><th>Email</th><th>Rôle</th><th>Analyses</th><th>Alertes</th><th>Actions</th></tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr><td colSpan={8} style={{ textAlign: 'center', color: 'var(--mut)', padding: 20 }}>Aucun utilisateur</td></tr>
              ) : users.map((u, i) => (
                <tr key={u.username}>
                  <td style={{ color: 'var(--mut)', fontSize: 11 }}>{i + 1}</td>
                  <td style={{ fontWeight: 700, fontFamily: 'var(--mono)', fontSize: 12 }}>{u.username}</td>
                  <td>{u.name ?? '—'}</td>
                  <td style={{ fontSize: 11 }}>{u.email ?? '—'}</td>
                  <td>
                    <span style={{ color: ROLE_COLORS[u.role] ?? '#94a3b8', fontWeight: 700, fontSize: 12 }}>
                      {u.role}
                    </span>
                  </td>
                  <td>{u.n_analyses ?? 0}</td>
                  <td>{u.n_pending_alerts ?? u.n_alerts ?? 0}</td>
                  <td>
                    {u.username !== 'admin' && (
                      <button className="btn bd bxs" onClick={() => del(u)}>🗑</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {modal && (
        <div className="ov" onClick={() => setModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="mhead">
              <span style={{ fontWeight: 700 }}>+ Nouvel utilisateur</span>
              <button className="mclose" onClick={() => setModal(false)}>×</button>
            </div>
            <div className="mbody">
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {[
                  { f: 'username', l: 'Identifiant', p: 'ex: jdupont' },
                  { f: 'password', l: 'Mot de passe', p: '••••••••', t: 'password' },
                  { f: 'name',     l: 'Nom complet',  p: 'Jean Dupont' },
                  { f: 'email',    l: 'Email',         p: 'j.dupont@aba.com', t: 'email' },
                ].map(({ f, l, p, t }) => (
                  <div className="fg" key={f}>
                    <label>{l}</label>
                    <input type={t ?? 'text'} placeholder={p}
                      value={(form as any)[f]}
                      onChange={e => setForm(fm => ({ ...fm, [f]: e.target.value }))}
                    />
                  </div>
                ))}
              </div>
              <div className="fg">
                <label>Rôle</label>
                <select value={form.role} onChange={e => setForm(f => ({ ...f, role: e.target.value }))}>
                  <option value="analyst">Analyst</option>
                  <option value="consultant">Consultant</option>
                  <option value="admin">Admin</option>
                </select>
              </div>
            </div>
            <div className="mfoot">
              <button className="btn bg-btn bsm" onClick={() => setModal(false)}>Annuler</button>
              <button className="btn bp bsm" onClick={save}>💾 Créer</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
