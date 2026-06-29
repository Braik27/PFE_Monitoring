import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../../contexts/AuthContext'
import { useToast } from '../../contexts/ToastContext'
import styles from './Login.module.css'

export default function Login() {
  const { login, user, isLoading: authLoading } = useAuth()
  const { showToast } = useToast()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!authLoading && user) {
      navigate('/', { replace: true })
    }
  }, [authLoading, user, navigate])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username || !password) {
      showToast('Veuillez remplir tous les champs', 'warning')
      return
    }
    setLoading(true)
    try {
      await login(username, password)
      navigate('/')
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { error?: string } } })?.response?.data?.error
      showToast(msg ?? 'Identifiants incorrects', 'error')
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
          Surveillance des <span className={styles.accent}>Flux</span> en temps réel
        </h1>
        <p className={styles.lead}>
          Plateforme de rapprochement et de contrôle des interfaces comptables Cegid ↔ Oracle
          pour ABA Luxury — suivi des écarts, alertes et reporting consolidé.
        </p>
        <div className={styles.tags}>
          <span className={styles.tag}><span className={styles.tagDot} />RETAIL</span>
          <span className={styles.tag}><span className={styles.tagDot} />FASHION</span>
          <span className={styles.tag}><span className={styles.tagDot} />DIGITAL</span>
        </div>
      </section>

      <section className={styles.right} aria-label="Connexion">
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
          <h2 className={styles.title}>Flux Monitor</h2>
          <p className={styles.sub}>Connectez-vous à votre espace de surveillance</p>
          <div className={styles.divider} />

          <form onSubmit={handleSubmit} className={styles.form}>
            <div>
              <label className={styles.fieldLbl} htmlFor="login-user">NOM D&apos;UTILISATEUR</label>
              <div className={styles.inputWrap}>
                <span className={styles.inputIcon} aria-hidden>👤</span>
                <input
                  id="login-user"
                  type="text"
                  autoComplete="username"
                  placeholder="ex. admin"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoFocus
                />
              </div>
            </div>
            <div>
              <label className={styles.fieldLbl} htmlFor="login-pass">MOT DE PASSE</label>
              <div className={styles.inputWrap}>
                <span className={styles.inputIcon} aria-hidden>🔒</span>
                <input
                  id="login-pass"
                  type={showPwd ? 'text' : 'password'}
                  autoComplete="current-password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className={styles.togglePwd}
                  onClick={() => setShowPwd((v) => !v)}
                  aria-label={showPwd ? 'Masquer le mot de passe' : 'Afficher le mot de passe'}
                >
                  {showPwd ? '🙈' : '👁'}
                </button>
              </div>
            </div>
            <div className={styles.forgotRow}>
              <a className={styles.forgot} href="#" onClick={(e) => e.preventDefault()}>
                Mot de passe oublié ?
              </a>
            </div>
            <button type="submit" className={styles.submitBtn} disabled={loading || authLoading}>
              {loading ? <span className="spin" style={{ width: 20, height: 20, borderWidth: 2 }} /> : (
                <>🚪 Se connecter</>
              )}
            </button>
          </form>

          <div className={styles.footer}>
            TimSoft Group © {new Date().getFullYear()} — Solutions Business Intelligence
          </div>
        </div>
      </section>
    </div>
  )
}
