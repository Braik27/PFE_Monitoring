import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useToast } from '../../contexts/ToastContext'
import api from '../../lib/api'
import styles from './ForgotPassword.module.css'

export default function ForgotPassword() {
  const { showToast } = useToast()
  const [email, setEmail] = useState('')
  const [submitted, setSubmitted] = useState(false)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!email) {
      showToast('Veuillez saisir votre email', 'warning')
      return
    }
    setLoading(true)
    try {
      await api.post('/api/auth/forgot-password', { email })
      setSubmitted(true)
    } catch {
      setSubmitted(true)
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
          Réinitialisation du <span className={styles.accent}>mot de passe</span>
        </h1>
        <p className={styles.lead}>
          Saisissez votre adresse email et nous vous enverrons un lien sécurisé
          pour réinitialiser votre mot de passe.
        </p>
      </section>

      <section className={styles.right} aria-label="Mot de passe oublié">
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
          <h2 className={styles.title}>Mot de passe oublié ?</h2>
          <p className={styles.sub}>
            {submitted
              ? 'Si cette adresse est associée à un compte, un email de réinitialisation a été envoyé.'
              : "Saisissez votre email pour recevoir le lien de réinitialisation."}
          </p>

          {!submitted ? (
            <form onSubmit={handleSubmit} className={styles.form}>
              <div>
                <label className={styles.fieldLbl} htmlFor="fp-email">ADRESSE EMAIL</label>
                <div className={styles.inputWrap}>
                  <span className={styles.inputIcon} aria-hidden>📧</span>
                  <input
                    id="fp-email"
                    type="email"
                    autoComplete="email"
                    placeholder="ex. utilisateur@entreprise.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    autoFocus
                    disabled={loading}
                  />
                </div>
              </div>
              <button type="submit" className={styles.submitBtn} disabled={loading}>
                {loading ? <span className="spin" style={{ width: 20, height: 20, borderWidth: 2 }} /> : 'Envoyer le lien'}
              </button>
            </form>
          ) : (
            <div className={styles.successBox}>
              <span className={styles.successIcon}>✅</span>
              <div>
                <div className={styles.successTitle}>Email envoyé</div>
                <div className={styles.successText}>
                  Vérifiez votre boîte de réception (et le dossier spam).
                </div>
              </div>
            </div>
          )}

          <div className={styles.footer}>
            <Link className={styles.backLink} to="/login">
              ← Retour à la connexion
            </Link>
          </div>
        </div>
      </section>
    </div>
  )
}
