import styles from './PageLoader.module.css'

export default function PageLoader() {
  return (
    <div className={styles.container}>
      <div className={styles.spinner} />
      <p className={styles.text}>Chargement...</p>
    </div>
  )
}
