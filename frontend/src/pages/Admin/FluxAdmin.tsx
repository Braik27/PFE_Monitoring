import { useEffect, useState, useCallback } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'

export default function FluxAdmin() {
  const { showToast } = useToast()
  const [fluxes, setFluxes] = useState<any[]>([])
  const [modal, setModal] = useState(false)
  const [editing, setEditing] = useState<any>(null)
  const [form, setForm] = useState({ flux_id: '', name: '', direction: 'EXPORT', key_columns: '', divisions: '' })

  const load = useCallback(async () => {
    try {
      const res = await api.get('/api/flux')
      const arr = Array.isArray(res.data) ? res.data : []
      setFluxes(arr)
    } catch { showToast('Erreur chargement flux', 'error') }
  }, [showToast])

  useEffect(() => { load() }, [load])

  const openModal = (flux?: any) => {
    if (flux) {
      setEditing(flux)
      setForm({
        flux_id: flux.flux_id, name: flux.name ?? flux.flux_name, direction: String(flux.direction ?? 'EXPORT').toUpperCase(),
        key_columns: (flux.key_columns ?? []).join(', '),
        divisions: (flux.divisions ?? []).join(', '),
      })
    } else {
      setEditing(null)
      setForm({ flux_id: '', name: '', direction: 'EXPORT', key_columns: '', divisions: '' })
    }
    setModal(true)
  }

  const save = async () => {
    try {
      const payload = {
        ...form,
        flux_name: form.name,
        key_columns: form.key_columns.split(',').map(s => s.trim()).filter(Boolean),
        divisions:   form.divisions.split(',').map(s => s.trim()).filter(Boolean),
      }
      if (editing) {
        await api.put(`/api/flux/${editing.flux_id}`, payload)
      } else {
        await api.post('/api/flux', payload)
      }
      showToast('Flux sauvegardé', 'success')
      setModal(false); load()
    } catch (e: any) { showToast(e?.response?.data?.error ?? 'Erreur', 'error') }
  }

  const del = async (id: string) => {
    if (!confirm('Supprimer ce flux ?')) return
    try { await api.delete(`/api/flux/${id}`); showToast('Flux supprimé', 'success'); load() }
    catch { showToast('Erreur suppression', 'error') }
  }

  const F = (p: any) => (
    <div className="fg">
      <label>{p.label}</label>
      {p.type === 'select'
        ? <select value={form[p.field as keyof typeof form]} onChange={e => setForm(f => ({ ...f, [p.field]: e.target.value }))}>
            <option value="EXPORT">EXPORT</option><option value="IMPORT">IMPORT</option>
          </select>
        : <input placeholder={p.placeholder} value={form[p.field as keyof typeof form]}
            onChange={e => setForm(f => ({ ...f, [p.field]: e.target.value }))} />
      }
    </div>
  )

  return (
    <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <div style={{ fontSize: 12, color: 'var(--mut)' }}>Configurez les interfaces Cegid ↔ Oracle</div>
        <button className="btn bp" onClick={() => openModal()}>+ Nouveau flux</button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(290px,1fr))', gap: 14 }}>
        {fluxes.map(f => (
          <div key={f.flux_id} style={{ background: '#fff', border: '1.5px solid var(--brd)', borderRadius: 'var(--r)', overflow: 'hidden', boxShadow: 'var(--sh)' }}>
            <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--brd)' }}>
              <div style={{ width: 34, height: 34, borderRadius: 8, background: String(f.direction).toUpperCase() === 'EXPORT' ? 'var(--blu-lt)' : 'var(--pur-lt)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15 }}>
                {String(f.direction).toUpperCase() === 'EXPORT' ? '📤' : '📥'}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 700, fontSize: 13 }}>{f.name ?? f.flux_name}</div>
                <div style={{ fontSize: 11, color: 'var(--mut)' }}>{f.flux_id}</div>
              </div>
              <span className={`bdg ${String(f.direction).toUpperCase() === 'EXPORT' ? 'b-b' : 'b-p'}`} style={{ fontSize: 10 }}>{String(f.direction).toUpperCase()}</span>
            </div>
            <div style={{ padding: '12px 14px' }}>
              {f.key_columns?.length > 0 && (
                <div style={{ fontSize: 11, color: 'var(--mut)', marginBottom: 6 }}>
                  Clés : {f.key_columns.map((k: string) => <span key={k} style={{ background: 'var(--blu-lt)', color: 'var(--blue)', fontSize: 10, padding: '1px 6px', borderRadius: 4, marginRight: 4, fontFamily: 'var(--mono)' }}>{k}</span>)}
                </div>
              )}
              {f.divisions?.length > 0 && (
                <div style={{ fontSize: 11, color: 'var(--mut)' }}>Divisions : {f.divisions.join(', ')}</div>
              )}
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6, padding: '8px 14px', borderTop: '1px solid var(--brd)', background: 'var(--s2)' }}>
              <button className="btn bg-btn bxs" onClick={() => openModal(f)}>✏️ Modifier</button>
              <button className="btn bd bxs" onClick={() => del(f.flux_id)}>🗑 Supprimer</button>
            </div>
          </div>
        ))}
      </div>

      {modal && (
        <div className="ov" onClick={() => setModal(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="mhead">
              <span style={{ fontWeight: 700 }}>{editing ? '✏️ Modifier le flux' : '+ Nouveau flux'}</span>
              <button className="mclose" onClick={() => setModal(false)}>×</button>
            </div>
            <div className="mbody">
              <F field="flux_id" label="Identifiant (flux_id)" placeholder="ex: JRNL_KWT" />
              <F field="name" label="Nom affiché" placeholder="ex: Journal KWT" />
              <F field="direction" label="Direction" type="select" />
              <F field="key_columns" label="Colonnes clés (séparées par virgule)" placeholder="JOURNAL_NAME, PERIOD_NAME" />
              <F field="divisions" label="Divisions (séparées par virgule)" placeholder="KSA, KWT, SPG, DOHA" />
            </div>
            <div className="mfoot">
              <button className="btn bg-btn bsm" onClick={() => setModal(false)}>Annuler</button>
              <button className="btn bp bsm" onClick={save}>💾 Sauvegarder</button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
