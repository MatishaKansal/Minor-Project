import './loading.css'

export default function LoadingScreen({ title = 'Welcome back!', message = 'Loading your workspace…' }) {
  return (
    <main className="ledger-loading" role="status" aria-live="polite">
      <div className="ledger-loading-content">
        <div className="ledger-loading-brand">
          <span className="ledger-loading-brand-icon" aria-hidden="true">
            <svg viewBox="0 0 32 32" fill="currentColor"><rect x="7" y="18" width="4" height="8" rx="1" /><rect x="13" y="13" width="4" height="13" rx="1" /><rect x="19" y="8" width="4" height="18" rx="1" /><rect x="25" y="5" width="3" height="21" rx="1" /></svg>
          </span>
          <span>Ledgerline</span>
        </div>
        <div className="ledger-loading-greeting" aria-hidden="true">
          <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M17 25V12a2.5 2.5 0 0 1 5 0v9-13a2.5 2.5 0 0 1 5 0v13-11a2.5 2.5 0 0 1 5 0v12-8a2.5 2.5 0 0 1 5 0v12c0 10-5 15-13 15-4 0-7-2-10-6l-5-7a2.7 2.7 0 0 1 4-3l4 4" />
            <path d="M10 10 8 8m5-1-1-3M5 15l-3-1m38-4 2-2m-3 15 3 1m-2-9 3-1" />
          </svg>
        </div>
        <h1 className="ledger-loading-title">{title}</h1>
        <p className="ledger-loading-message">{message}</p>
        <div className="ledger-loading-track" role="progressbar" aria-label={message}>
          <span className="ledger-loading-indicator" />
        </div>
      </div>
    </main>
  )
}
