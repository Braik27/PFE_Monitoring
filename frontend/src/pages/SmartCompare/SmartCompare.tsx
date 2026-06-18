import { useState, useRef } from 'react'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import AsyncAnalysisProgress from '../../components/AsyncAnalysisProgress'

// ── Types ──────────────────────────────────────────────────────────────────
interface ColInfo  { nom: string; type: string; uniq_ratio: number; sample: string[] }
interface MapRow   { cegid_col: string; oracle_col: string | null; confiance: number; source: string; key: boolean }
interface PreviewData {
  cols_cegid:  ColInfo[]
  cols_oracle: ColInfo[]
  mapping:     MapRow[]
  key_cols:    string[]
  flux_key:    string
  ia_summary?: string
}

// L'API peut retourner différents noms selon la route (/run vs /run-async)
// On accepte tous les alias possibles
interface Result {
  label?:           string
  // concordance
  concordance_rate?: number
  concordance?:      number
  // lignes
  n_rows_a?:   number
  n_cegid?:    number
  n_rows_b?:   number
  n_oracle?:   number
  // correspondances
  n_matched?:  number
  matched?:    number
  // manquants
  n_missing_b?:   number
  n_only_cegid?:  number
  missing_b?:     number
  // excédents
  n_extra_b?:     number
  n_only_oracle?: number
  extra_b?:       number
  // écarts valeur
  n_value_diff?:  number
  n_warnings?:    number
  value_diff?:    number
  // anomalies
  anomalies?: any[]
  n_anomalies?: number
}

// Lit le premier champ non-nul parmi les clés fournies
function rv(r: Result, ...keys: string[]): number {
  for (const k of keys) {
    const v = (r as any)[k]
    if (v !== undefined && v !== null && v !== '') return Number(v)
  }
  return 0
}

// ── Step indicator ─────────────────────────────────────────────────────────
function Steps({ current }: { current: 1 | 2 | 3 | 4 }) {
  const steps = ['Upload', 'Analyse', 'Mapping', 'Résultats']
  return (
    <div style={{ display: 'flex', alignItems: 'center', marginBottom: 24 }}>
      {steps.map((s, i) => {
        const n      = i + 1
        const done   = n < current
        const active = n === current
        return (
          <>
            <div key={s} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '8px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600,
              background: active ? 'var(--blue)' : done ? 'var(--grn-lt)' : 'transparent',
              color:      active ? '#fff'        : done ? 'var(--green)'  : 'var(--mut)',
              transition: 'all .25s',
            }}>
              <div style={{
                width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 11, fontWeight: 800,
                background: active ? 'rgba(255,255,255,.25)' : done ? 'var(--green)' : 'rgba(0,0,0,.08)',
                color: done ? '#fff' : 'inherit',
              }}>{done ? '✓' : n}</div>
              <span>{s}</span>
            </div>
            {i < steps.length - 1 && (
              <div key={`sep-${i}`} style={{ flex: 1, height: 1, background: 'var(--brd)', margin: '0 4px' }} />
            )}
          </>
        )
      })}
    </div>
  )
}

// ── DropZone ────────────────────────────────────────────────────────────────
function DropZone({ file, onFile, label, inputRef }: {
  file: File | null
  onFile: (f: File) => void
  label: string
  inputRef: React.RefObject<HTMLInputElement>
}) {
  const [drag, setDrag] = useState(false)
  const ok = !!file
  return (
    <div
      style={{
        border: `2px dashed ${ok ? 'var(--green)' : drag ? 'var(--blue)' : 'var(--brd)'}`,
        borderStyle: ok ? 'solid' : 'dashed',
        background: ok ? 'var(--grn-lt)' : drag ? 'var(--blu-lt)' : '#fff',
        borderRadius: 'var(--r)', padding: '28px 20px', textAlign: 'center',
        cursor: 'pointer', transition: 'all .2s', flex: 1, position: 'relative',
      }}
      onClick={() => inputRef.current?.click()}
      onDragOver={e => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={e => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files[0]; if (f) onFile(f) }}
    >
      <input ref={inputRef} type="file" accept=".csv,.xlsx,.xls"
        style={{ position: 'absolute', inset: 0, opacity: 0, cursor: 'pointer', width: '100%', height: '100%' }}
        onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f) }} />
      <div style={{ fontSize: 32, marginBottom: 8, color: ok ? 'var(--green)' : 'var(--mut)' }}>
        {ok ? '✅' : '📄'}
      </div>
      <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>{label}</div>
      {file
        ? <div style={{ fontSize: 12, color: 'var(--green)', fontWeight: 600, fontFamily: 'var(--mono)' }}>{file.name}</div>
        : <div style={{ fontSize: 11, color: 'var(--mut)' }}>CSV ou Excel (.xlsx) — glisser ou cliquer</div>
      }
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────
export default function SmartCompare() {
  const { showToast } = useToast()
  const [file1,   setFile1]   = useState<File | null>(null)
  const [file2,   setFile2]   = useState<File | null>(null)
  const [useIA,   setUseIA]   = useState(true)
  const [loading, setLoading] = useState(false)
  const [step,    setStep]    = useState<1 | 2 | 3 | 4>(1)

  const [preview,  setPreview]  = useState<PreviewData | null>(null)
  const [mapping,  setMapping]  = useState<MapRow[]>([])
  const [keyCols,  setKeyCols]  = useState<Set<string>>(new Set())
  const [result,   setResult]   = useState<Result | null>(null)
  const [loadMsg,  setLoadMsg]  = useState('Analyse en cours…')

  const [asyncFormData, setAsyncFormData] = useState<FormData | null>(null)
  const [asyncKey,      setAsyncKey]      = useState(0)
  const [isComparing,   setIsComparing]   = useState(false)

  const ref1 = useRef<HTMLInputElement>(null!)
  const ref2 = useRef<HTMLInputElement>(null!)

  const concColor = (r: number) =>
    r >= 95 ? 'var(--green)' : r >= 80 ? 'var(--orange)' : 'var(--red)'

  // ── Étape 1 → 2+3 : preview ───────────────────────────────────────
  const handleAnalyze = async () => {
    if (!file1 || !file2) return showToast('Importez les deux fichiers', 'warning')
    setLoading(true)
    setLoadMsg('Lecture des fichiers et détection des colonnes…')
    setStep(2)
    try {
      const fd = new FormData()
      fd.append('cegid',   file1)
      fd.append('oracle',  file2)
      fd.append('use_ia',  useIA ? 'true' : 'false')
      const res = await api.post('/api/smart/preview', fd)
      const data: PreviewData = res.data
      setPreview(data)
      setMapping(data.mapping ?? [])
      setKeyCols(new Set<string>(data.key_cols ?? []))
      setStep(3)
    } catch (e: any) {
      showToast(e?.response?.data?.error ?? 'Erreur aperçu', 'error')
      setStep(1)
    } finally {
      setLoading(false)
    }
  }

  // ── Étape 3 → async comparison ────────────────────────────────────
  const handleRun = () => {
    if (!preview || !file1 || !file2) return

    const selectedKeys = Array.from(keyCols)
    const keyColsPair  = selectedKeys
      .map(cegid_col => {
        const m = mapping.find(m => m.cegid_col === cegid_col)
        return { cegid_col, oracle_col: m?.oracle_col ?? null }
      })
      .filter(p => p.oracle_col != null)

    const config = {
      mapping:  mapping.map(m => ({ cegid_col: m.cegid_col, oracle_col: m.oracle_col, key: m.key ?? false })),
      key_cols: keyColsPair,
      flux_key: preview.flux_key,
    }

    const fd = new FormData()
    fd.append('cegid',  file1)
    fd.append('oracle', file2)
    fd.append('config', JSON.stringify(config))

    setAsyncFormData(fd)
    setAsyncKey(k => k + 1)   // force remount → startedRef reset
    setIsComparing(true)
  }

  const handleAsyncComplete = (data: any) => {
    setResult(data)
    setStep(4)
    setIsComparing(false)
    setAsyncFormData(null)
    showToast('Comparaison terminée !', 'success')
  }

  const handleAsyncError = (err: string) => {
    showToast(err ?? 'Erreur comparaison', 'error')
    setIsComparing(false)
    setAsyncFormData(null)
  }

  const toggleKey = (col: string) => {
    setKeyCols(prev => {
      const next = new Set(prev)
      next.has(col) ? next.delete(col) : next.add(col)
      return next
    })
  }

  const updateMapping = (idx: number, oracle_col: string | null) => {
    setMapping(prev =>
      prev.map((m, i) =>
        i === idx ? { ...m, oracle_col, confiance: oracle_col ? 100 : 0, source: 'manuel' } : m
      )
    )
  }

  const reset = () => {
    setFile1(null); setFile2(null); setPreview(null); setMapping([])
    setKeyCols(new Set()); setResult(null); setStep(1)
    setIsComparing(false); setAsyncFormData(null)
  }

  // ── Render ─────────────────────────────────────────────────────────
  return (
          <div style={{ maxWidth: 960 }}>

        <Steps current={step} />

        {/* ── ÉTAPE 1 : Upload ─────────────────────────────────────── */}
        {step === 1 && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20 }}>
              <DropZone label="Fichier Cegid"  file={file1} onFile={setFile1} inputRef={ref1} />
              <DropZone label="Fichier Oracle" file={file2} onFile={setFile2} inputRef={ref2} />
            </div>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12,
              fontWeight: 600, cursor: 'pointer', color: 'var(--txt2)', marginBottom: 16 }}>
              <input type="checkbox" checked={useIA} onChange={e => setUseIA(e.target.checked)}
                style={{ width: 15, height: 15, accentColor: 'var(--purple)' }} />
              Enrichissement IA (Claude) — détection sémantique des colonnes
            </label>
            <button className="btn bp" onClick={handleAnalyze} disabled={!file1 || !file2}>
              🔍 Analyser les fichiers
            </button>
          </>
        )}

        {/* ── LOADING (étape 2) ─────────────────────────────────────── */}
        {loading && (
          <div style={{ textAlign: 'center', padding: '40px 0' }}>
            <div className="spin" style={{ width: 32, height: 32, borderWidth: 3,
              borderTopColor: 'var(--blue)', borderColor: 'var(--brd)', margin: '0 auto 12px' }} />
            <div style={{ fontSize: 13, fontWeight: 600 }}>{loadMsg}</div>
            <div style={{ fontSize: 11, color: 'var(--mut)', marginTop: 4 }}>Veuillez patienter…</div>
          </div>
        )}

        {/* ── COMPARAISON ASYNC ─────────────────────────────────────── */}
        {isComparing && asyncFormData && (
          <div style={{ marginBottom: 20 }}>
            <AsyncAnalysisProgress
              key={asyncKey}
              formData={asyncFormData}
              onComplete={handleAsyncComplete}
              onError={handleAsyncError}
            />
          </div>
        )}

        {/* ── ÉTAPE 3 : Mapping ───────────────────────────────────── */}
        {step === 3 && !loading && preview && !isComparing && (
          <>
            {preview.ia_summary && (
              <div style={{
                background: 'linear-gradient(135deg,var(--pur-lt),var(--blu-lt))',
                border: '1px solid var(--pur-md)', borderRadius: 10, padding: '12px 16px',
                display: 'flex', gap: 12, marginBottom: 16,
              }}>
                <div style={{ fontSize: 22 }}>🧠</div>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--purple)', marginBottom: 4 }}>
                    Analyse IA — Claude
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--txt2)', lineHeight: 1.5 }}>
                    {preview.ia_summary}
                  </div>
                </div>
              </div>
            )}

            {/* Colonnes */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
              {[
                { title: 'Colonnes Cegid',  icon: '📋', cols: preview.cols_cegid,  bg: '#dbeafe' },
                { title: 'Colonnes Oracle', icon: '🗃️', cols: preview.cols_oracle, bg: '#dcfce7' },
              ].map(({ title, icon, cols, bg }) => (
                <div key={title} className="sblk">
                  <div className="sblk-h">
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ width: 34, height: 34, borderRadius: 8, background: bg,
                        display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 16 }}>
                        {icon}
                      </div>
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 700 }}>{title}</div>
                        <div style={{ fontSize: 11, color: 'var(--mut)' }}>{cols?.length ?? 0} colonnes détectées</div>
                      </div>
                    </div>
                  </div>
                  <div style={{ padding: 12, display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill,minmax(180px,1fr))', gap: 8 }}>
                    {(cols ?? []).map(c => (
                      <div key={c.nom} style={{
                        background: 'var(--s2)', border: '1px solid var(--brd)', borderRadius: 9,
                        padding: '8px 10px',
                        borderLeft: `3px solid ${keyCols.has(c.nom) ? 'var(--green)' : 'var(--brd)'}`,
                      }}>
                        <div style={{ fontSize: 12, fontWeight: 700, fontFamily: 'var(--mono)',
                          marginBottom: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {c.nom}
                        </div>
                        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                          <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, fontWeight: 600,
                            background: c.type === 'decimal' ? 'var(--blu-lt)' : c.type === 'date' ? 'var(--orn-lt)' : '#f1f5f9',
                            color:      c.type === 'decimal' ? 'var(--blue)'   : c.type === 'date' ? 'var(--orange)' : '#475569',
                          }}>{c.type}</span>
                          {keyCols.has(c.nom) && (
                            <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4,
                              fontWeight: 600, background: 'var(--grn-lt)', color: 'var(--green)' }}>
                              clé
                            </span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>

            {/* Mapping table */}
            <div className="sblk" style={{ marginBottom: 16 }}>
              <div className="sblk-h">
                <span style={{ fontSize: 13, fontWeight: 700 }}>🔗 Mapping des colonnes</span>
                <span style={{ fontSize: 11, color: 'var(--mut)' }}>{mapping.length} colonnes mappées</span>
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th>Colonne Cegid</th>
                      <th>Colonne Oracle</th>
                      <th>Confiance</th>
                      <th>Source</th>
                      <th>Clé ?</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mapping.map((m, i) => (
                      <tr key={i}>
                        <td><span style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{m.cegid_col}</span></td>
                        <td>
                          <select value={m.oracle_col ?? ''} onChange={e => updateMapping(i, e.target.value || null)}
                            style={{ border: '1.5px solid var(--brd)', borderRadius: 6,
                              padding: '4px 8px', fontSize: 12, background: '#fff' }}>
                            <option value="">— non mappé —</option>
                            {(preview.cols_oracle ?? []).map(c => (
                              <option key={c.nom} value={c.nom}>{c.nom}</option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <span className={`bdg ${m.confiance >= 80 ? 'b-g' : m.confiance >= 50 ? 'b-o' : 'b-r'}`}>
                            {m.confiance}%
                          </span>
                        </td>
                        <td style={{ fontSize: 11, color: 'var(--mut)' }}>{m.source}</td>
                        <td>
                          <input type="checkbox" checked={keyCols.has(m.cegid_col)}
                            onChange={() => toggleKey(m.cegid_col)}
                            style={{ accentColor: 'var(--green)', width: 15, height: 15 }} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn bp" onClick={handleRun} disabled={loading}>
                🚀 Lancer la comparaison intelligente
              </button>
              <button className="btn bg-btn" onClick={reset}>Réinitialiser</button>
            </div>
          </>
        )}

        {/* ── ÉTAPE 4 : Résultats ─────────────────────────────────── */}
        {step === 4 && result && (() => {
          const concordance = rv(result, 'concordance_rate', 'concordance')
          const nCegid      = rv(result, 'n_rows_a',   'n_cegid')
          const nOracle     = rv(result, 'n_rows_b',   'n_oracle')
          const nMatched    = rv(result, 'n_matched',  'matched')
          const nMissing    = rv(result, 'n_missing_b','n_only_cegid', 'missing_b')
          const nExtra      = rv(result, 'n_extra_b',  'n_only_oracle','extra_b')
          const nDiff       = rv(result, 'n_value_diff','n_warnings',  'value_diff')

          return (
            <>
              {/* Score header */}
              <div style={{
                background: 'linear-gradient(135deg,#0f172a,#1e3a5f)', color: '#fff',
                borderRadius: 'var(--r)', padding: '20px 24px', marginBottom: 16,
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              }}>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>
                    {result.label ?? 'Résultat Smart Compare'}
                  </div>
                  <div style={{ fontSize: 12, color: 'rgba(255,255,255,.5)' }}>
                    {nCegid.toLocaleString()} lignes Cegid ·{' '}
                    {nOracle.toLocaleString()} lignes Oracle ·{' '}
                    {nMatched.toLocaleString()} correspondances
                  </div>
                </div>
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 44, fontWeight: 800, color: concColor(concordance), lineHeight: 1 }}>
                    {concordance}%
                  </div>
                  <div style={{ fontSize: 11, color: 'rgba(255,255,255,.5)', marginTop: 2 }}>concordance</div>
                </div>
              </div>

              {/* KPIs */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12, marginBottom: 16 }}>
                {[
                  { l: 'Correspondances', v: nMatched, c: 'var(--green)'  },
                  { l: 'Manquants (B)',   v: nMissing, c: 'var(--red)'    },
                  { l: 'Excédents (B)',   v: nExtra,   c: 'var(--orange)' },
                  { l: 'Écarts valeur',   v: nDiff,    c: 'var(--purple)' },
                ].map(s => (
                  <div key={s.l} style={{
                    background: '#fff', border: '1.5px solid var(--brd)',
                    borderRadius: 'var(--r)', padding: 14, textAlign: 'center',
                    boxShadow: 'var(--sh)',
                  }}>
                    <div style={{ fontSize: 10, color: 'var(--mut)', fontWeight: 700,
                      textTransform: 'uppercase', letterSpacing: '.5px', marginBottom: 6 }}>
                      {s.l}
                    </div>
                    <div style={{ fontSize: 26, fontWeight: 800, color: s.c }}>
                      {s.v.toLocaleString()}
                    </div>
                  </div>
                ))}
              </div>

              {/* Anomalies */}
              {(result.anomalies ?? []).length > 0 && (
                <div className="sblk">
                  <div className="sblk-h">
                    <span style={{ fontSize: 13, fontWeight: 700 }}>🔍 Anomalies détectées</span>
                    <span className="bdg b-r">
                      {(result.n_anomalies ?? result.anomalies!.length).toLocaleString()}
                    </span>
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table>
                      <thead>
                        <tr>
                          <th>Sévérité</th>
                          <th>Type</th>
                          <th>Clé</th>
                          <th>Valeur Cegid</th>
                          <th>Valeur Oracle</th>
                          <th>Détail</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.anomalies!.slice(0, 200).map((a: any, i: number) => (
                          <tr key={i}>
                            <td>
                              <span className={`bdg ${
                                a.severity === 'CRITICAL' || a.severity === 'CRITIQUE' ? 'b-r' : 'b-o'
                              }`}>
                                {a.severity}
                              </span>
                            </td>
                            <td style={{ fontSize: 11 }}>{a.type}</td>
                            <td>
                              <span className="val-box">
                                {typeof a.key === 'object'
                                  ? (a.key_str ?? JSON.stringify(a.key))
                                  : (a.key ?? '—')}
                              </span>
                            </td>
                            <td><span className="val-box">{a.val_cegid ?? a.val_a ?? '—'}</span></td>
                            <td><span className="val-box">{a.val_oracle ?? a.val_b ?? '—'}</span></td>
                            <td style={{ fontSize: 11 }}>{a.message ?? a.detail ?? '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {result.anomalies!.length > 200 && (
                    <div style={{ padding: '10px 16px', fontSize: 11, color: 'var(--mut)',
                      borderTop: '1px solid var(--brd)', textAlign: 'center' }}>
                      Affichage limité à 200 sur {result.anomalies!.length.toLocaleString()} anomalies
                    </div>
                  )}
                </div>
              )}

              <button className="btn bg-btn" onClick={reset} style={{ marginTop: 8 }}>
                🔄 Nouvelle comparaison
              </button>
            </>
          )
        })()}

      </div>
  )
}