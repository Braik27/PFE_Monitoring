import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './Dashboard.module.css'

// ── Types ──────────────────────────────────────────────────────────────────
interface FluxCfg {
  flux_id: string; flux_name: string; icon: string; color: string
  direction: string; frequency: string; objective: string
  main_rule: string; key_columns: string[]
  n_analyses: number; total_critiques: number; total_warnings: number
  concordance_moy: number; last_analysis: any | null
}
interface HistRow {
  id: number; flux_id: string; label: string; created_at: string
  summary: {
    flux_name?: string; analyst?: string; division?: string
    divisions_found?: string[]; concordance_moyenne?: number
    total_critiques?: number; total_warnings?: number
    pairs?: PairData[]
  }
}
interface PairData {
  division?: string; n_cegid?: number; n_oracle?: number
  n_matched?: number; n_missing_oracle?: number; n_missing_cegid?: number
  n_critiques?: number; n_warnings?: number; concordance?: number
  top_error_columns?: { column: string; n_errors: number }[]
  anomalies?: any[]
}

const DIV_FLAGS: Record<string, string> = {
  KSA: '🇸🇦', KWT: '🇰🇼', SPG: '🇸🇬', DOHA: '🇶🇦', GLOBAL: '🌐', LUX: '🇱🇺',
}
const divLabel = (d: string) => (DIV_FLAGS[d] ? `${DIV_FLAGS[d]} ${d}` : d)
const concColor = (r: number) => r >= 95 ? 'var(--green)' : r >= 80 ? 'var(--orange)' : 'var(--red)'
const fmt = (s: string) => s ? new Date(s).toLocaleString('fr-FR', { day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit' }) : '—'

// ── Concordance Gauge (signature element) ──────────────────────────────────
function ConcordanceGauge({ score, totalFlux, totalCrit, totalWarn }: { score: number; totalFlux: number; totalCrit: number; totalWarn: number }) {
  const angle = Math.round((score / 100) * 360)
  const color = score >= 90 ? 'var(--green)' : score >= 75 ? 'var(--gold)' : score >= 60 ? 'var(--orange)' : 'var(--red)'

  return (
    <div className={styles.gaugeWrap}>
      <div className={styles.gaugeRing}>
        <div className={styles.gaugeRingBg} />
        <div className={styles.gaugeRingFill} style={{
          background: `conic-gradient(${color} 0deg, ${color} ${angle}deg, transparent ${angle}deg, transparent 360deg)`,
          mask: 'radial-gradient(circle at 50% 50%, transparent 38px, #000 39px)',
          WebkitMask: 'radial-gradient(circle at 50% 50%, transparent 38px, #000 39px)',
        }} />
        <div className={styles.gaugeInner}>
          <div className={styles.gaugeScore} style={{ color }}>{score}%</div>
          <div className={styles.gaugeLabel}>Concordance</div>
        </div>
      </div>
      <div className={styles.gaugeInfo}>
        <div className={styles.gaugeTitle}>Synthese des flux</div>
        <div className={styles.gaugeSub}>Vue d'ensemble de la concordance entre Cegid et Oracle</div>
        <div className={styles.gaugeStats}>
          <div className={styles.gaugeStat}>
            <div className={styles.gaugeStatVal}>{totalFlux}</div>
            <div className={styles.gaugeStatLbl}>Flux</div>
          </div>
          <div className={styles.gaugeStat}>
            <div className={styles.gaugeStatVal} style={{ color: 'var(--red)' }}>{totalCrit}</div>
            <div className={styles.gaugeStatLbl}>Critiques</div>
          </div>
          <div className={styles.gaugeStat}>
            <div className={styles.gaugeStatVal} style={{ color: 'var(--orange)' }}>{totalWarn}</div>
            <div className={styles.gaugeStatLbl}>Warnings</div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── KPI top card ───────────────────────────────────────────────────────────
function KCard({ bar, ico, icoBg, lbl, val, valColor, sub, prog }: any) {
  return (
    <div className={styles.kcard}>
      <div className={styles.kbar} style={{ background: bar }} />
      <div className={styles.kico} style={{ background: icoBg }}>{ico}</div>
      <div className={styles.klbl}>{lbl}</div>
      <div className={styles.kval} style={{ color: valColor }}>{val}</div>
      {prog != null
        ? <div className={styles.progWrap}><div className={styles.progFill} style={{ background: valColor, width: `${prog}%` }} /></div>
        : <div className={styles.ksub}>{sub}</div>
      }
    </div>
  )
}

// ── Flux Card ──────────────────────────────────────────────────────────────
function FluxCard({ cfg, row, onNavigate }: { cfg: FluxCfg; row: HistRow | null; onNavigate: () => void }) {
  const [open, setOpen] = useState(false)
  const s      = row?.summary ?? {}
  const pairs  = s.pairs ?? []
  const conc   = s.concordance_moyenne ?? (pairs[0]?.concordance ?? 100)
  const crit   = s.total_critiques ?? pairs.reduce((a, p) => a + (p.n_critiques ?? 0), 0)
  const warn   = s.total_warnings  ?? pairs.reduce((a, p) => a + (p.n_warnings  ?? 0), 0)
  const analyst = s.analyst ?? '—'
  const divsFound = s.divisions_found ?? (s.division ? [s.division] : [])
  const cc     = concColor(conc)
  const hasManyPairs = pairs.length > 1

  const totCegid  = pairs.reduce((a, p) => a + (p.n_cegid ?? 0), 0)
  const totOracle = pairs.reduce((a, p) => a + (p.n_oracle ?? 0), 0)
  const totMatch  = pairs.reduce((a, p) => a + (p.n_matched ?? 0), 0)
  const totAbsO   = pairs.reduce((a, p) => a + (p.n_missing_oracle ?? 0), 0)
  const totAbsC   = pairs.reduce((a, p) => a + (p.n_missing_cegid ?? 0), 0)

  const isImport  = cfg.direction?.toLowerCase() === 'import'
  const healthLbl = crit > 0 ? '🔴 CRITICAL' : warn > 0 ? '🟡 WARNING' : '🟢 HEALTHY'
  const score     = Math.max(0, Math.round(conc - crit * 5))

  return (
    <div className={styles.fcard}>
      {/* ── Carte d'identité (dark refined) ── */}
      <div style={{
        background: 'linear-gradient(135deg,#0B1420,#162030)', color: '#fff',
        borderRadius: 'var(--r) var(--r) 0 0', padding: 22, position: 'relative', overflow: 'hidden',
      }}>
        <div style={{ position: 'absolute', top: -50, right: -50, width: 160, height: 160, background: 'rgba(201,169,110,.04)', borderRadius: '50%' }} />

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: 18, flexWrap: 'wrap', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', fontSize: 20, fontWeight: 800 }}>
            <span>{cfg.icon ?? '📊'}</span>
            <span>{cfg.flux_name}</span>
            <span style={{
              fontSize: 10, fontWeight: 700, padding: '3px 10px', borderRadius: 20, letterSpacing: '.5px',
              background: isImport ? 'rgba(124,58,237,.25)' : 'rgba(201,169,110,.2)',
              color: isImport ? '#c4b5fd' : 'var(--gold-md)',
              border: `1px solid ${isImport ? 'rgba(124,58,237,.35)' : 'rgba(201,169,110,.3)'}`,
            }}>{isImport ? '📥 Import Oracle→Cegid' : '📤 Export Cegid→Oracle'}</span>
            {divsFound.map(d => (
              <span key={d} style={{ fontSize: 10, fontWeight: 700, padding: '3px 10px', borderRadius: 20, background: 'rgba(255,255,255,.08)', color: 'rgba(255,255,255,.8)', border: '1px solid rgba(255,255,255,.12)' }}>
                {divLabel(d)}
              </span>
            ))}
          </div>
          <button
            className="btn bxs"
            style={{ borderColor: 'rgba(201,169,110,.25)', color: 'var(--gold-md)', background: 'rgba(201,169,110,.08)', fontSize: 11 }}
            onClick={onNavigate}
          >+ Nouvelle analyse</button>
        </div>

        {/* Infos grille 3×2 */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 16 }}>
          {[
            { l: 'Fréquence',        v: cfg.frequency ?? 'Quotidienne à 08h00' },
            { l: 'Clés de matching', v: (cfg.key_columns ?? []).join(' + ') || '—', mono: true },
            { l: 'Dernière analyse', v: row ? fmt(row.created_at) : '—' },
            { l: 'Objectif métier',  v: cfg.objective ?? '—' },
            { l: 'Règle principale', v: cfg.main_rule ?? '—' },
            { l: 'Analyste',         v: analyst },
          ].map(({ l, v, mono }) => (
            <div key={l}>
              <label style={{ fontSize: 9, color: 'rgba(255,255,255,.35)', textTransform: 'uppercase', letterSpacing: '.8px', display: 'block', marginBottom: 3 }}>{l}</label>
              <span style={{ fontSize: 12, fontWeight: 600, color: 'rgba(255,255,255,.85)', fontFamily: mono ? 'var(--mono)' : undefined }}>{v}</span>
            </div>
          ))}
        </div>

        {/* Health bar */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, background: 'rgba(255,255,255,.04)', borderRadius: 8, padding: '12px 16px', border: '1px solid rgba(255,255,255,.06)' }}>
          <div style={{
            width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
            background: crit > 0 ? '#f87171' : warn > 0 ? '#fbbf24' : '#4ade80',
            animation: 'pulse 2s ease-in-out infinite',
          }} />
          <div style={{ fontSize: 13, fontWeight: 700 }}>{healthLbl}</div>
          {crit > 0 && <span style={{ fontSize: 11, color: 'rgba(255,255,255,.45)', marginLeft: 8 }}>{crit} erreur(s) critique(s)</span>}
          <span style={{ marginLeft: 'auto', fontSize: 24, fontWeight: 800, color: 'var(--gold)' }}>{score}/100</span>
        </div>
      </div>

      {/* ── Grille stats lignes ── */}
      {row && (
        <>
          <div className={styles.fcGrid}>
            <div className={styles.fcCell}>
              <div className={styles.fcCellLbl}>📂 Lignes Cegid</div>
              <div className={styles.fcCellVal} style={{ color: 'var(--blue)' }}>{hasManyPairs ? totCegid : (pairs[0]?.n_cegid ?? '—')}</div>
              {hasManyPairs && <div style={{ fontSize: 10, color: 'var(--mut)', marginTop: 3 }}>{pairs.length} divisions</div>}
            </div>
            <div className={styles.fcCell}>
              <div className={styles.fcCellLbl}>🗄️ Lignes Oracle</div>
              <div className={styles.fcCellVal} style={{ color: 'var(--purple)' }}>{hasManyPairs ? totOracle : (pairs[0]?.n_oracle ?? '—')}</div>
            </div>
            <div className={styles.fcCell}>
              <div className={styles.fcCellLbl}>✅ Concordance</div>
              <div className={styles.fcCellVal} style={{ color: cc }}>{conc}%</div>
              <div className={styles.progWrap}><div className={styles.progFill} style={{ background: cc, width: `${conc}%` }} /></div>
            </div>
          </div>

          {/* ── Barre stats ── */}
          <div className={styles.fcStats}>
            {[
              { v: hasManyPairs ? totMatch  : (pairs[0]?.n_matched ?? 0),        l: 'Matchées',   c: 'var(--blue)' },
              { v: hasManyPairs ? totAbsO   : (pairs[0]?.n_missing_oracle ?? 0), l: 'Abs. Oracle', c: 'var(--red)' },
              { v: hasManyPairs ? totAbsC   : (pairs[0]?.n_missing_cegid ?? 0),  l: 'Abs. Cegid',  c: 'var(--purple)' },
              { v: crit,                                                           l: 'Critiques',   c: 'var(--red)' },
              { v: warn,                                                           l: 'Warnings',    c: 'var(--orange)' },
              { v: conc + '%',                                                     l: 'Concordance', c: cc },
            ].map(({ v, l, c }) => (
              <div key={l} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: c, lineHeight: 1, marginBottom: 2 }}>{v}</div>
                <div style={{ fontSize: 9, color: 'var(--mut)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.3px' }}>{l}</div>
              </div>
            ))}
          </div>

          {/* ── Top colonnes erreur ── */}
          {(pairs[0]?.top_error_columns ?? []).length > 0 && (
            <div style={{ padding: '10px 18px', borderBottom: '1px solid var(--brd)', background: 'var(--s2)', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 4 }}>
              <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--txt2)' }}>Top colonnes en erreur : </span>
              {(pairs[0].top_error_columns ?? []).map(c => (
                <span key={c.column} className="bdg b-r" style={{ marginLeft: 4 }}>{c.column} <b>{c.n_errors}</b></span>
              ))}
            </div>
          )}

          {/* ── Bouton Voir détail / Alertes ── */}
          <div style={{ padding: '12px 20px', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button
              className="btn bsm"
              style={{ background: 'var(--red-lt)', color: 'var(--red)', border: '1px solid var(--red-md)' }}
              onClick={() => window.location.href = '/alerts'}
            >🔔 Voir les alertes</button>
            <button className="btn bg-btn bsm" onClick={() => setOpen(o => !o)}>
              {open ? '▲ Masquer détails' : '▼ Voir détails'}
            </button>
          </div>

          {/* ── Détail anomalies ── */}
          {open && (pairs[0]?.anomalies ?? []).length > 0 && (
            <div style={{ padding: '0 20px 16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 0 8px' }}>
                <span style={{ fontSize: 12, fontWeight: 700 }}>🔍 Anomalies</span>
                <span className="bdg b-x">{(pairs[0].anomalies ?? []).length} anomalie(s)</span>
              </div>
              <div style={{ overflowX: 'auto' }}>
                <table>
                  <thead><tr><th>Sévérité</th><th>Type</th><th>Clé</th><th>Cegid</th><th>Oracle</th><th>Explication</th></tr></thead>
                  <tbody>
                    {(pairs[0].anomalies ?? []).slice(0, 15).map((a: any, i: number) => (
                      <tr key={i}>
                        <td><span className={`bdg ${a.severity === 'CRITIQUE' ? 'b-r' : 'b-o'}`}>{a.severity}</span></td>
                        <td><span style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{a.error_type}</span></td>
                        <td style={{ fontSize: 11 }}>{Object.entries(a.key_values ?? {}).map(([k, v]) => `${k}=${v}`).join(' | ')}</td>
                        <td><span className="val-box">{a.val_cegid ?? '—'}</span></td>
                        <td><span className="val-box">{a.val_oracle ?? '—'}</span></td>
                        <td style={{ fontSize: 11, color: 'var(--mut)' }}>{a.explication ?? ''}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}

      {!row && (
        <div style={{ padding: 20, textAlign: 'center', color: 'var(--mut)', fontSize: 12 }}>
          Aucune analyse pour ce flux — <button className="btn bp bxs" onClick={onNavigate}>Lancer une analyse</button>
        </div>
      )}
    </div>
  )
}

// ── Main Dashboard ─────────────────────────────────────────────────────────
export default function Dashboard() {
  const { showToast } = useToast()
  const navigate = useNavigate()

  const [fluxCfgs,  setFluxCfgs]  = useState<FluxCfg[]>([])
  const [lastRows,  setLastRows]   = useState<Record<string, HistRow>>({})
  const [loading,   setLoading]    = useState(true)

  const load = async () => {
    setLoading(true)
    try {
      const [statsRes, histRes] = await Promise.all([
        api.get('/api/stats'),
        api.get('/api/history', { params: { limit: 200 } }),
      ])
      const cfgs: FluxCfg[] = Array.isArray(statsRes.data) ? statsRes.data : []
      setFluxCfgs(cfgs)

      // Garder la dernière analyse par flux_id
      const rows: Record<string, HistRow> = {}
      const hist: HistRow[] = Array.isArray(histRes.data) ? histRes.data : []
      for (const r of hist) {
        if (!rows[r.flux_id]) rows[r.flux_id] = r
      }
      setLastRows(rows)
    } catch (e) {
      showToast('Erreur chargement dashboard', 'error')
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  // KPI globaux calculés
  const totalFlux = fluxCfgs.filter(f => lastRows[f.flux_id]).length
  const avgConc   = totalFlux
    ? Math.round(fluxCfgs.filter(f => lastRows[f.flux_id]).reduce((s, f) => {
        const r = lastRows[f.flux_id]
        return s + (r?.summary?.concordance_moyenne ?? 100)
      }, 0) / totalFlux)
    : 0
  const totalCrit = fluxCfgs.reduce((s, f) => s + (lastRows[f.flux_id]?.summary?.total_critiques ?? 0), 0)
  const totalWarn = fluxCfgs.reduce((s, f) => s + (lastRows[f.flux_id]?.summary?.total_warnings ?? 0), 0)

  return (
    <>
      {/* ── Concordance Gauge ── */}
      {!loading && totalFlux > 0 && (
        <ConcordanceGauge
          score={avgConc}
          totalFlux={totalFlux}
          totalCrit={totalCrit}
          totalWarn={totalWarn}
        />
      )}

      {/* ── KPI Row ── */}
      {!loading && totalFlux > 0 && (
        <div className={styles.krow}>
          <KCard bar="var(--blue)"   ico="⚡" icoBg="var(--blu-lt)" lbl="Flux analysés"     val={totalFlux}  sub="résultats actifs" />
          <KCard bar="var(--green)"  ico="✅" icoBg="var(--grn-lt)" lbl="Concordance globale" val={avgConc+'%'} valColor={concColor(avgConc)} prog={avgConc} />
          <KCard bar="var(--red)"    ico="🚨" icoBg="var(--red-lt)" lbl="Erreurs critiques"  val={totalCrit}  valColor="var(--red)"    sub="sur tous les flux" />
          <KCard bar="var(--orange)" ico="⚠️" icoBg="var(--orn-lt)" lbl="Warnings"           val={totalWarn}  valColor="var(--orange)" sub="à surveiller" />
        </div>
      )}

      {/* ── Contenu ── */}
      {loading ? (
        <div className={styles.loadingWrap}>
          <div className="spin" style={{ width: 28, height: 28, borderWidth: 3, borderTopColor: 'var(--blue)', borderColor: 'var(--brd)' }} />
          <span style={{ color: 'var(--mut)', fontSize: 13 }}>Chargement...</span>
        </div>
      ) : fluxCfgs.length === 0 ? (
        <div className="sblk">
          <div className="empty">
            <div className="empty-ico">📭</div>
            <div className="empty-txt">Aucune analyse disponible</div>
            <div className="empty-sub">Cliquez sur « Nouvelle analyse » pour commencer</div>
            <button className="btn bp bsm" style={{ marginTop: 16 }} onClick={() => navigate('/analyze')}>
              🔍 Nouvelle analyse
            </button>
          </div>
        </div>
      ) : (
        fluxCfgs.map(cfg => (
          <FluxCard
            key={cfg.flux_id}
            cfg={cfg}
            row={lastRows[cfg.flux_id] ?? null}
            onNavigate={() => navigate('/analyze')}
          />
        ))
      )}
    </>
  )
}