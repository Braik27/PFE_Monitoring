import { Component, type ErrorInfo, type ReactNode } from 'react'
import styles from './ErrorBoundary.module.css'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error.message, info.componentStack)
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      const isChunkError = this.state.error?.message?.includes('Loading chunk')
      return (
        <div className={styles.container}>
          <div className={styles.icon}>{isChunkError ? '📡' : '⚠️'}</div>
          <h2 className={styles.title}>
            {isChunkError ? 'Échec du chargement' : 'Une erreur est survenue'}
          </h2>
          <p className={styles.message}>
            {isChunkError
              ? 'Le module n\'a pas pu être chargé. Vérifiez votre connexion réseau et réessayez.'
              : this.state.error?.message || 'Erreur inattendue'}
          </p>
          <button className={styles.button} onClick={this.handleRetry}>
            Réessayer
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
