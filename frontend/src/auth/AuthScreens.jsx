import { useState } from 'react'
import { useAuth } from './AuthContext.jsx'
import LoadingScreen from '../LoadingScreen.jsx'

export function SignInScreen() {
  const { configured, signIn } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await signIn(username.trim(), password)
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
          <h2>Make sense of your finances, in one place.</h2>
          <p>Bring your records together, check for issues and understand what needs your attention.</p>
        </div>
        <ul className="auth-points">
          <li>Keep your financial records organized</li>
          <li>Review possible issues with supporting documents</li>
          <li>Get a clear picture of your company's records</li>
        </ul>
      </aside>
      <form className="auth-card" onSubmit={submit}>
        <div className="audit-kicker">WELCOME BACK</div>
        <h1>Sign in</h1>
        <p>Use your individual account to access Ledgerline.</p>
        {!configured && <div className="auth-error" role="alert">Supabase frontend configuration is missing. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY.</div>}
        {error && <div className="auth-error" role="alert">{error}</div>}
        <label>Username<input autoComplete="username" required value={username} onChange={(event) => setUsername(event.target.value)} /></label>
        <p>Existing accounts can also sign in with their email.</p>
        <label>Password<input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} /></label>
        <button className="audit-button primary" disabled={!configured || busy}>{busy ? 'Please wait…' : 'Sign in securely'}</button>
      </form>
      </div>
    </main>
  )
}

export function AuthLoadingScreen() {
  return <LoadingScreen message="Checking your secure session…" />
}

export function ProfileAccessScreen() {
  const { user, profileError, signOut } = useAuth()
  return (
    <main className="auth-screen">
      <section className="auth-card">
        <div className="audit-kicker">ACCOUNT ACCESS</div>
        <h1>Profile access required</h1>
        <p>{profileError || 'Your account is authenticated, but it does not have a company profile assigned.'}</p>
        <p className="auth-account">{user?.email}</p>
        <p>Ask a superadmin to assign your business and role, then sign in again.</p>
        <button className="audit-button secondary" onClick={signOut}>Sign out</button>
      </section>
    </main>
  )
}
