import { useState, useEffect, type FormEvent } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './ResetPassword.module.css'

export default function ResetPassword() {
  const { showToast } = useToast()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [showPwd2, setShowPwd2] = useState(false)
  const [loading, setLoading] = useState(false)
  const [tokenVerified, setTokenVerified] = useState(false)
  const [verifying, setVerifying] = useState(!!token)

  useEffect(() => {
    if (token) {
      api.post('/api/auth/verify-reset-token', { token })
        .then(() => setTokenVerified(true))
        .catch(() => { showToast('Token invalide ou expiré', 'error') })
        .finally(() => setVerifying(false))
    }
  }, [token, showToast])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (password !== confirm) {
      showToast('Les mots de passe ne correspondent pas', 'warning')
      return
    }
    if (password.length < 6) {
      showToast('Le mot de passe doit contenir au moins 6 caractères', 'warning')
      return
    }
    setLoading(true)
    try {
      await api.post('/api/auth/reset-password', { token, password })
      showToast('Mot de passe réinitialisé avec succès', 'success')
      navigate('/login')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error
      showToast(msg ?? 'Erreur lors de la réinitialisation', 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={styles.page}>
      <section className={styles.hero} aria-label="Présentation">
        <div className={styles.heroTop}>
          <div className={styles.brandRow}>
            <em>tim</em>soft<span className={styles.brandDot} />
          </div>
          <span className={styles.badge}>FLUX MONITOR</span>
        </div>
        <h1 className={styles.headline}>
          Nouveau <span className={styles.accent}>mot de passe</span>
        </h1>
        <p className={styles.lead}>
          Saisissez votre nouveau mot de passe pour réinitialiser votre compte.
        </p>
      </section>

      <section className={styles.right} aria-label="Réinitialisation">
        <div className={styles.card}>
          <div className={styles.cardBrand}>
            <img
              src="/static/logo-timsofta.png"
              alt=""
              className={styles.logo}
              onError={(e) => { (e.target as HTMLImageElement).style.display = 'none' }}
            />
            <div className={styles.cardBrandText}>
              <em>tim</em>soft<span className={styles.brandDot} />
            </div>
          </div>
          <h2 className={styles.title}>Réinitialisation du mot de passe</h2>
          <p className={styles.sub}>
            {verifying
              ? 'Vérification du lien...'
              : !token
                ? 'Le lien de réinitialisation est manquant. Utilisez le lien envoyé par email.'
                : 'Saisissez et confirmez votre nouveau mot de passe.'}
          </p>

          {token && !verifying && (
            <form onSubmit={handleSubmit} className={styles.form}>
              <div>
                <label className={styles.fieldLbl} htmlFor="rp-pass">NOUVEAU MOT DE PASSE</label>
                <div className={styles.inputWrap}>
                  <span className={styles.inputIcon} aria-hidden>🔒</span>
                  <input
                    id="rp-pass"
                    type={showPwd ? 'text' : 'password'}
                    autoComplete="new-password"
                    placeholder="••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    disabled={loading || !tokenVerified}
                  />
                  <button
                    type="button"
                    className={styles.togglePwd}
                    onClick={() => setShowPwd((v) => !v)}
                    aria-label={showPwd ? 'Masquer' : 'Afficher'}
                  >
                    {showPwd ? '🙈' : '👁'}
                  </button>
                </div>
              </div>

              <div>
                <label className={styles.fieldLbl} htmlFor="rp-pass2">CONFIRMER LE MOT DE PASSE</label>
                <div className={styles.inputWrap}>
                  <span className={styles.inputIcon} aria-hidden>🔒</span>
                  <input
                    id="rp-pass2"
                    type={showPwd2 ? 'text' : 'password'}
                    autoComplete="new-password"
                    placeholder="••••••••"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    disabled={loading || !tokenVerified}
                  />
                  <button
                    type="button"
                    className={styles.togglePwd}
                    onClick={() => setShowPwd2((v) => !v)}
                    aria-label={showPwd2 ? 'Masquer' : 'Afficher'}
                  >
                    {showPwd2 ? '🙈' : '👁'}
                  </button>
                </div>
              </div>

              <button type="submit" className={styles.submitBtn} disabled={loading || !tokenVerified}>
                {loading ? <span className="spin" style={{ width: 20, height: 20, borderWidth: 2 }} /> : 'Réinitialiser'}
              </button>
            </form>
          )}

          <div className={styles.footer}>
            <button className={styles.backLink} onClick={() => navigate('/login')}>
              ← Retour à la connexion
            </button>
          </div>
        </div>
      </section>
    </div>
  )
}
