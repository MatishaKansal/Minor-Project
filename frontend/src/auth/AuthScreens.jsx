import { useState } from 'react'
import { useAuth } from './AuthContext.jsx'

export function SignInScreen() {
  const { configured, signIn } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await signIn(email.trim(), password)
    } catch (signInError) {
      setError(signInError.message || 'Sign in failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-screen">
      <div className="auth-layout">
      <aside className="auth-hero">
        <div className="auth-brand"><span className="brand-mark">L</span><strong>ledgerline</strong></div>
        <div>
          <h2>From evidence to verdict, in one workspace.</h2>
          <p>Link transactions to invoices, run deterministic audit checks and keep every decision attributed to a named reviewer.</p>
        </div>
        <ul className="auth-points">
          <li>Role-based access for auditors, reviewers and viewers</li>
          <li>Every finding decision recorded with the signed-in user</li>
          <li>Evidence-linked procedures and persistent findings</li>
        </ul>
      </aside>
      <form className="auth-card" onSubmit={submit}>
        <div className="audit-kicker">WELCOME BACK</div>
        <h1>Sign in</h1>
        <p>Use your individual account to access assigned audit records.</p>
        {!configured && <div className="auth-error" role="alert">Supabase frontend configuration is missing. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.</div>}
        {error && <div className="auth-error" role="alert">{error}</div>}
        <label>Email<input type="email" autoComplete="username" required value={email} onChange={(event) => setEmail(event.target.value)} /></label>
        <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <button className="audit-button primary" disabled={!configured || busy}>{busy ? 'Signing in…' : 'Sign in securely'}</button>
        <small>Accounts are provisioned by your administrator.</small>
      </form>
      </div>
    </main>
  )
}

export function AuthLoadingScreen() {
  return <main className="auth-screen"><div className="auth-loading">Checking your secure session…</div></main>
}

export function ProfileAccessScreen() {
  const { user, profileError, signOut } = useAuth()
  return (
    <main className="auth-screen">
      <section className="auth-card">
        <div className="audit-kicker">ACCOUNT ACCESS</div>
        <h1>Profile access required</h1>
        <p>{profileError || 'Your account is authenticated, but it does not have an audit profile assigned to a business.'}</p>
        <p className="auth-account">{user?.email}</p>
        <p>Ask an administrator to assign your business and role, then sign in again.</p>
        <button className="audit-button secondary" onClick={signOut}>Sign out</button>
      </section>
    </main>
  )
}
