import { useEffect, useState, useRef, type DragEvent, type RefObject } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './Analyze.module.css'

interface FluxConfig {
  flux_id: string
  name: string
  direction: string
  key_columns: string[]
  divisions: string[]
}

const DIVISIONS = [
  { code: '', label: '🌐 Toutes' },
  { code: 'KSA',  label: '🇸🇦 KSA' },
  { code: 'KWT',  label: '🇰🇼 KWT' },
  { code: 'SPG',  label: '🇸🇬 SPG' },
  { code: 'DOHA', label: '🇶🇦 DOHA' },
]

export default function Analyze() {
  const { showToast } = useToast()
  const [fluxList, setFluxList] = useState<FluxConfig[]>([])
  const [selectedFlux, setSelectedFlux] = useState('')
  const [selectedDiv, setSelectedDiv] = useState('')
  const [label, setLabel] = useState('')
  const [cegidFile, setCegidFile] = useState<File | null>(null)
  const [oracleFile, setOracleFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)
  const cRef = useRef<HTMLInputElement>(null)
  const oRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.get('/api/flux').then((r) => {
      const arr = Array.isArray(r.data) ? r.data : []
      setFluxList(arr.map((f: Record<string, unknown>) => ({
        ...f,
        name: f.name ?? f.flux_name,
        divisions: f.divisions ?? [],
      })) as unknown as FluxConfig[])
    }).catch(() => {})
  }, [])

  const currentFlux = fluxList.find(f => f.flux_id === selectedFlux)

  const handleDrop = (e: DragEvent, which: 'cegid' | 'oracle') => {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) which === 'cegid' ? setCegidFile(f) : setOracleFile(f)
  }

  const launch = async () => {
    if (!selectedFlux) return showToast('Sélectionnez un flux', 'warning')
    if (!cegidFile)    return showToast('Fichier Cegid manquant', 'warning')
    if (!oracleFile)   return showToast('Fichier Oracle manquant', 'warning')

    setLoading(true)
    setResult(null)
    try {
      const fd = new FormData()
      fd.append('flux_id', selectedFlux)
      fd.append('division', selectedDiv)
      fd.append('label', label)
      fd.append('cegid', cegidFile)
      fd.append('oracle', oracleFile)
      const res = await api.post('/api/analyze', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      setResult(res.data)
      showToast('Analyse terminée !', 'success')
    } catch (err: any) {
      showToast(err?.response?.data?.error ?? 'Erreur lors de l\'analyse', 'error')
    } finally {
      setLoading(false)
    }
  }

  const reset = () => {
    setResult(null); setCegidFile(null); setOracleFile(null)
    setSelectedFlux(''); setLabel(''); setSelectedDiv('')
  }

  return (
          <div className={styles.layout}>
        {/* Left form */}
        <div className={styles.form}>
          <h3>🔍 Nouvelle analyse</h3>

          <div className="fg">
            <label>Flux à analyser</label>
            <select value={selectedFlux} onChange={e => setSelectedFlux(e.target.value)}>
              <option value="">Sélectionner...</option>
              {fluxList.map(f => (
                <option key={f.flux_id} value={f.flux_id}>{f.name}</option>
              ))}
            </select>
          </div>

          {currentFlux && (
            <div className={styles.fluxInfo}>
              <div style={{ fontWeight: 700, color: 'var(--blue)', marginBottom: 4 }}>
                {currentFlux.name} — {currentFlux.direction}
              </div>
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                {currentFlux.key_columns?.map(k => (
                  <span key={k} className={styles.keyBadge}>{k}</span>
                ))}
              </div>
            </div>
          )}

          <div className="fg">
            <label>Division / Pays</label>
            <div className={styles.chips}>
              {DIVISIONS.map(d => (
                <span
                  key={d.code}
                  className={`${styles.chip} ${selectedDiv === d.code ? styles.chipOn : ''}`}
                  onClick={() => setSelectedDiv(d.code)}
                >
                  {d.label}
                </span>
              ))}
            </div>
          </div>

          <div className="fg">
            <label>Description</label>
            <input
              placeholder="ex: Ventes Avril 2026 — KSA"
              value={label}
              onChange={e => setLabel(e.target.value)}
            />
          </div>

          {/* Upload zones */}
          <div className={styles.uploadGrid}>
            <UploadZone
              label="Fichier Cegid"
              icon="📂"
              file={cegidFile}
              onFile={setCegidFile}
              onDrop={e => handleDrop(e, 'cegid')}
              inputRef={cRef}
            />
            <UploadZone
              label="Fichier Oracle"
              icon="🗄️"
              file={oracleFile}
              onFile={setOracleFile}
              onDrop={e => handleDrop(e, 'oracle')}
              inputRef={oRef}
            />
          </div>

          <button
            className="btn bp"
            style={{ width: '100%', justifyContent: 'center', padding: '12px', fontSize: 14 }}
            onClick={launch}
            disabled={loading}
          >
            {loading ? <><span className="spin" /> Analyse en cours...</> : '🚀 Lancer l\'analyse'}
          </button>
          {(result || cegidFile || oracleFile) && (
            <button className="btn bg-btn bsm" style={{ width: '100%', justifyContent: 'center', marginTop: 8 }} onClick={reset}>
              ✕ Réinitialiser
            </button>
          )}
        </div>

        {/* Right: results */}
        <div className={styles.results}>
          {loading && (
            <div className={styles.resultEmpty}>
              <div className="spin" style={{ width: 32, height: 32, borderWidth: 3, borderTopColor: 'var(--blue)', borderColor: 'var(--brd)' }} />
              <div style={{ color: 'var(--mut)', fontSize: 13 }}>Analyse en cours, veuillez patienter...</div>
            </div>
          )}
          {!loading && !result && (
            <div className={styles.resultEmpty}>
              <div style={{ fontSize: 48, marginBottom: 12 }}>🔍</div>
              <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--txt2)' }}>Résultats de l'analyse</div>
              <div style={{ fontSize: 12, color: 'var(--mut)' }}>Sélectionnez un flux, importez vos fichiers et lancez l'analyse</div>
            </div>
          )}
          {!loading && result && <AnalysisResult data={result} />}
        </div>
      </div>
  )
}

function UploadZone({ label, icon, file, onFile, onDrop, inputRef }: {
  label: string
  icon: string
  file: File | null
  onFile: (f: File | null) => void
  onDrop: (e: DragEvent) => void
  inputRef: RefObject<HTMLInputElement | null>
}) {
  const [drag, setDrag] = useState(false)
  return (
    <div
      className={`${styles.uzone} ${file ? styles.uzFilled : ''} ${drag ? styles.uzDrag : ''}`}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={e => { setDrag(false); onDrop(e) }}
    >
      <input
        type="file" ref={inputRef} style={{ display: 'none' }} accept=".csv,.xlsx,.xls"
        onChange={e => onFile(e.target.files?.[0] ?? null)}
      />
      <div style={{ fontSize: 26, marginBottom: 6 }}>{file ? '✅' : icon}</div>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 2 }}>{label}</div>
      {file
        ? <div style={{ fontSize: 11, color: 'var(--green)', fontWeight: 600 }}>{file.name}</div>
        : <div style={{ fontSize: 11, color: 'var(--mut)' }}>CSV / Excel</div>
      }
    </div>
  )
}

function AnalysisResult({ data }: { data: any }) {
  const conc = data.concordance_rate ?? data.concordance_moyenne ?? 0
  const color = conc >= 95 ? 'var(--green)' : conc >= 80 ? 'var(--orange)' : 'var(--red)'
  const pair = data.pairs?.[0] ?? {}
  const anomalies = data.anomalies ?? pair.anomalies ?? []
  const n_cegid = data.n_cegid ?? pair.n_cegid ?? '—'
  const n_oracle = data.n_oracle ?? pair.n_oracle ?? '—'
  const n_matched = data.n_matched ?? pair.n_matched ?? '—'
  const n_critiques = data.n_critiques ?? data.total_critiques ?? pair.n_critiques ?? 0
  const n_warnings = data.n_warnings ?? data.total_warnings ?? pair.n_warnings ?? 0
  const n_missing_oracle = data.n_missing_oracle ?? pair.n_missing_oracle ?? 0
  const n_extra_oracle = data.n_extra_oracle ?? 0
  const n_amount_diff = data.n_amount_diff ?? 0

  return (
    <div>
      {/* Identity card */}
      <div className={styles.idCard}>
        <div className={styles.idHeader}>
          <div style={{ fontSize: 20, fontWeight: 800 }}>
            {data.flux_name ?? data.flux_id}
            {data.division && <span className={styles.idBadge}>{data.division}</span>}
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 36, fontWeight: 800, color }}>{conc}%</div>
            <div style={{ fontSize: 11, color: 'rgba(255,255,255,.5)' }}>concordance</div>
          </div>
        </div>
        <div className={styles.idGrid}>
          <div className={styles.idItem}><label>Cegid</label><span>{n_cegid ?? '—'} lignes</span></div>
          <div className={styles.idItem}><label>Oracle</label><span>{n_oracle ?? '—'} lignes</span></div>
          <div className={styles.idItem}><label>Concordantes</label><span style={{ color: '#4ade80' }}>{n_matched ?? '—'}</span></div>
        </div>
        <div className={styles.idHealth}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: color, display: 'inline-block', animation: 'pulse 2s ease-in-out infinite' }} />
          <span style={{ flex: 1, fontSize: 12 }}>
            {conc >= 95 ? 'Flux en bonne santé' : conc >= 80 ? 'Avertissements détectés' : 'Anomalies critiques détectées'}
          </span>
          <span style={{ fontWeight: 800, fontSize: 22 }}>{conc}%</span>
        </div>
      </div>

      {/* Stats */}
      <div className={styles.statsRow}>
        {[
          { v: n_critiques, l: 'Critiques',      c: 'var(--red)' },
          { v: n_warnings,  l: 'Warnings',       c: 'var(--orange)' },
          { v: n_missing_oracle, l: 'Manquants Oracle', c: 'var(--blue)' },
          { v: n_extra_oracle,   l: 'Excédents Oracle', c: 'var(--purple)' },
          { v: n_amount_diff,    l: 'Écarts montant',   c: 'var(--red)' },
          { v: n_matched,        l: 'Concordantes',     c: 'var(--green)' },
        ].map(s => (
          <div key={s.l} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: s.c, lineHeight: 1, marginBottom: 2 }}>{s.v}</div>
            <div style={{ fontSize: 9, color: 'var(--mut)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.3px' }}>{s.l}</div>
          </div>
        ))}
      </div>

      {/* Anomalies table */}
      {anomalies.length > 0 && (
        <div className="sblk" style={{ marginTop: 16 }}>
          <div className="sblk-h">
            <span style={{ fontSize: 13, fontWeight: 700 }}>🔍 Anomalies détectées</span>
            <span className="bdg b-r">{anomalies.length}</span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Sévérité</th><th>Type</th><th>Clé</th><th>Cegid</th><th>Oracle</th><th>Explication</th>
                </tr>
              </thead>
              <tbody>
                {anomalies.slice(0, 100).map((a: any, i: number) => (
                  <tr key={i}>
                    <td>
                      <span className={`bdg ${a.severity === 'CRITIQUE' ? 'b-r' : a.severity === 'WARNING' ? 'b-o' : 'b-x'}`}>
                        {a.severity}
                      </span>
                    </td>
                    <td>{a.error_type ?? a.type}</td>
                    <td><span className="val-box">{a.key_str ?? a.key ?? '—'}</span></td>
                    <td><span className="val-box">{a.val_cegid ?? a.cegid_val ?? '—'}</span></td>
                    <td><span className="val-box">{a.val_oracle ?? a.oracle_val ?? '—'}</span></td>
                    <td style={{ fontSize: 11 }}>{a.explication ?? a.message ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
