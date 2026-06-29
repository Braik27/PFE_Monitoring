import { useEffect, useState, useRef, type DragEvent, type RefObject } from 'react'
import { useToast } from '../../contexts/ToastContext'
import { useAsyncJob } from '../../hooks/useAsyncJob'
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
  const [fluxList, setFluxList]     = useState<FluxConfig[]>([])
  const [selectedFlux, setSelectedFlux] = useState('')
  const [selectedDiv, setSelectedDiv]   = useState('')
  const [label, setLabel]               = useState('')
  const [cegidFile, setCegidFile]       = useState<File | null>(null)
  const [oracleFile, setOracleFile]     = useState<File | null>(null)
  const cRef = useRef<HTMLInputElement>(null)
  const oRef = useRef<HTMLInputElement>(null)

  // ── Hook async ──────────────────────────────────────────────────
  const { submit, status, progress, stepLabel, result, error, reset: resetJob } = useAsyncJob()

  const loading = status === 'PENDING' || status === 'RUNNING'

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

  // Afficher une notification quand terminé ou erreur
  useEffect(() => {
    if (status === 'DONE')  showToast('Analyse terminée !', 'success')
    if (status === 'ERROR') showToast(error ?? 'Erreur lors de l\'analyse', 'error')
  }, [status])

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

    const fd = new FormData()
    fd.append('flux_id',  selectedFlux)
    fd.append('division', selectedDiv)
    fd.append('label',    label)
    fd.append('analyst',  'braik')
    fd.append('cegid',    cegidFile)
    fd.append('oracle',   oracleFile)

    try {
      await submit(fd)
    } catch (_) {
      // erreur déjà gérée dans useAsyncJob + useEffect ci-dessus
    }
  }

  const reset = () => {
    resetJob()
    setCegidFile(null)
    setOracleFile(null)
    setSelectedFlux('')
    setLabel('')
    setSelectedDiv('')
  }

  return (
    <div className={styles.layout}>
      {/* ── Formulaire gauche ── */}
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

        <div className={styles.uploadGrid}>
          <UploadZone label="Fichier Cegid"  icon="📂" file={cegidFile}
            onFile={setCegidFile} onDrop={e => handleDrop(e, 'cegid')} inputRef={cRef} />
          <UploadZone label="Fichier Oracle" icon="🗄️" file={oracleFile}
            onFile={setOracleFile} onDrop={e => handleDrop(e, 'oracle')} inputRef={oRef} />
        </div>

        <button
          className="btn bp"
          style={{ width: '100%', justifyContent: 'center', padding: '12px', fontSize: 14 }}
          onClick={launch}
          disabled={loading}
        >
          {loading
            ? <><span className="spin" /> {stepLabel || 'Analyse en cours...'}</>
            : '🚀 Lancer l\'analyse'}
        </button>

        {(result || cegidFile || oracleFile) && (
          <button
            className="btn bg-btn bsm"
            style={{ width: '100%', justifyContent: 'center', marginTop: 8 }}
            onClick={reset}
          >
            ✕ Réinitialiser
          </button>
        )}
      </div>

      {/* ── Résultats droite ── */}
      <div className={styles.results}>

        {/* État idle */}
        {status === 'idle' && (
          <div className={styles.resultEmpty}>
            <div style={{ fontSize: 48, marginBottom: 12 }}>🔍</div>
            <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--txt2)' }}>
              Résultats de l'analyse
            </div>
            <div style={{ fontSize: 12, color: 'var(--mut)' }}>
              Sélectionnez un flux, importez vos fichiers et lancez l'analyse
            </div>
          </div>
        )}

        {/* Progression en temps réel */}
        {(status === 'PENDING' || status === 'RUNNING') && (
          <div className={styles.resultEmpty}>
            {/* Spinner */}
            <div className="spin" style={{
              width: 40, height: 40, borderWidth: 3,
              borderTopColor: 'var(--blue)', borderColor: 'var(--brd)',
              marginBottom: 20
            }} />

            {/* Étape courante */}
            <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--txt)', marginBottom: 8 }}>
              {stepLabel || 'Analyse en cours...'}
            </div>

            {/* Barre de progression */}
            <div style={{
              width: '100%', maxWidth: 320,
              height: 6, borderRadius: 999,
              background: 'var(--brd)', overflow: 'hidden',
              marginBottom: 8
            }}>
              <div style={{
                height: '100%',
                width: `${progress}%`,
                background: 'var(--blue)',
                borderRadius: 999,
                transition: 'width 0.4s ease'
              }} />
            </div>

            <div style={{ fontSize: 12, color: 'var(--mut)' }}>
              {progress}% — L'analyse tourne en arrière-plan
            </div>
          </div>
        )}

        {/* Erreur */}
        {status === 'ERROR' && (
          <div className={styles.resultEmpty}>
            <div style={{ fontSize: 40, marginBottom: 12 }}>❌</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--red)', marginBottom: 8 }}>
              Erreur lors de l'analyse
            </div>
            <div style={{
              fontSize: 12, color: 'var(--mut)',
              background: 'var(--bg-danger)', borderRadius: 8,
              padding: '8px 12px', maxWidth: 400,
              textAlign: 'left', wordBreak: 'break-word'
            }}>
              {error}
            </div>
          </div>
        )}

        {/* Résultat */}
        {status === 'DONE' && result && (
          <AnalysisResult data={result} />
        )}
      </div>
    </div>
  )
}

// ── Composants utilitaires ──────────────────────────────────────────

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
        aria-label="Fichier d'analyse"
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
  const stats      = data.stats ?? {}
  const ecarts     = data.ecarts ?? []
  const nb_crit    = data.nb_critique ?? 0
  const nb_warn    = data.nb_warning  ?? 0
  const color      = nb_crit === 0 ? 'var(--green)' : nb_crit < 10 ? 'var(--orange)' : 'var(--red)'

  // Télécharger Excel de cette analyse
  const downloadExcel = async () => {
    if (!data.analysis_id) return
    try {
      const res = await fetch(
        `/api/analysis/${data.analysis_id}/export/excel`,
        { credentials: 'include' }
      )
      const blob = await res.blob()
      const link = document.createElement('a')
      link.href  = URL.createObjectURL(blob)
      link.download = `analyse_${data.flux_id}_${data.analysis_id}.xlsx`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch { }
  }

  // Télécharger rapport par division
  const downloadByDivision = async () => {
    try {
      const res = await fetch('/api/report/by-division', { credentials: 'include' })
      const blob = await res.blob()
      const link = document.createElement('a')
      link.href  = URL.createObjectURL(blob)
      link.download = `rapport_divisions_${new Date().toISOString().slice(0,10)}.zip`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch { }
  }

  return (
    <div>
      {/* Carte identité */}
      <div className={styles.idCard}>
        <div className={styles.idHeader}>
          <div style={{ fontSize: 20, fontWeight: 800 }}>
            Flux {data.flux_id}
          </div>

          {/* Boutons export */}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            {data.analysis_id && (
              <button
                className="btn bsm"
                onClick={downloadExcel}
                style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}
              >
                ⬇ Excel analyse
              </button>
            )}
            <button
              className="btn bsm"
              onClick={downloadByDivision}
              style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}
            >
              🗂 Rapport par division
            </button>
            <button
              className="btn bsm"
              onClick={async () => {
                try {
                  const res = await fetch('/api/report/daily', { credentials: 'include' })
                  const blob = await res.blob()
                  const link = document.createElement('a')
                  link.href = URL.createObjectURL(blob)
                  link.download = `rapport_daily_${new Date().toISOString().slice(0,10)}.xlsx`
                  link.click()
                  URL.revokeObjectURL(link.href)
                } catch { }
              }}
              style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 4 }}
            >
              📅 Rapport du jour
            </button>
          </div>
        </div>

        <div style={{
          fontSize: 13, color: 'rgba(255,255,255,.7)',
          marginTop: 8, marginBottom: 12
        }}>
          {data.resume}
        </div>

        <div className={styles.idGrid}>
          <div className={styles.idItem}>
            <label>Lignes Cegid</label>
            <span>{stats.nb_lignes_cegid ?? '—'}</span>
          </div>
          <div className={styles.idItem}>
            <label>Lignes Oracle</label>
            <span>{stats.nb_lignes_oracle ?? '—'}</span>
          </div>
          <div className={styles.idItem}>
            <label>Écarts totaux</label>
            <span style={{ color: 'var(--red)' }}>{stats.nb_ecarts ?? '—'}</span>
          </div>
        </div>

        <div className={styles.idHealth}>
          <span style={{
            width: 10, height: 10, borderRadius: '50%',
            background: color, display: 'inline-block'
          }} />
          <span style={{ flex: 1, fontSize: 12 }}>
            {nb_crit === 0
              ? 'Aucune anomalie critique'
              : `${nb_crit} anomalie(s) critique(s) — ${nb_warn} warning(s)`}
          </span>
          {data.analysis_id && (
            <span style={{ fontSize: 11, color: 'rgba(255,255,255,.4)' }}>
              #{data.analysis_id}
            </span>
          )}
        </div>
      </div>

      {/* Stats — reste identique */}
      <div className={styles.statsRow}>
        {[
          { v: nb_crit,                      l: 'Critiques',      c: 'var(--red)' },
          { v: nb_warn,                      l: 'Warnings',       c: 'var(--orange)' },
          { v: stats.nb_doublons      ?? 0,  l: 'Doublons',       c: 'var(--purple)' },
          { v: stats.nb_absents_oracle ?? 0, l: 'Absents Oracle', c: 'var(--blue)' },
          { v: stats.nb_absents_cegid  ?? 0, l: 'Absents Cegid',  c: 'var(--blue)' },
          { v: stats.nb_ecarts        ?? 0,  l: 'Écarts total',   c: 'var(--mut)' },
        ].map(s => (
          <div key={s.l} style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 20, fontWeight: 800, color: s.c, lineHeight: 1, marginBottom: 2 }}>
              {s.v}
            </div>
            <div style={{ fontSize: 9, color: 'var(--mut)', fontWeight: 700,
              textTransform: 'uppercase', letterSpacing: '.3px' }}>
              {s.l}
            </div>
          </div>
        ))}
      </div>

      {/* Table écarts — identique */}
      {ecarts.length > 0 && (
        <div className="sblk" style={{ marginTop: 16 }}>
          <div className="sblk-h">
            <span style={{ fontSize: 13, fontWeight: 700 }}>🔍 Écarts détectés</span>
            <span className="bdg b-r">{ecarts.length}</span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table>
              <thead>
                <tr>
                  <th>Sévérité</th><th>Type</th><th>Article</th>
                  <th>Colonne</th><th>Cegid</th><th>Oracle</th>
                </tr>
              </thead>
              <tbody>
                {ecarts.slice(0, 100).map((e: any, i: number) => (
                  <tr key={i}>
                    <td>
                      <span className={`bdg ${
                        e.conseil?.severite === 'critique' ? 'b-r' :
                        e.conseil?.severite === 'warning'  ? 'b-o' : 'b-x'
                      }`}>
                        {e.conseil?.badge ?? e.type_ecart}
                      </span>
                    </td>
                    <td style={{ fontSize: 11 }}>{e.type_ecart}</td>
                    <td><span className="val-box">{e.article_id ?? '—'}</span></td>
                    <td style={{ fontSize: 11 }}>{e.colonne ?? '—'}</td>
                    <td><span className="val-box">{String(e.valeur_cegid ?? '—')}</span></td>
                    <td><span className="val-box">{String(e.valeur_oracle ?? '—')}</span></td>
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