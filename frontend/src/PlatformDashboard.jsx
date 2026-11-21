import { useCallback, useEffect, useState } from 'react'
import { useAuth } from './auth/AuthContext.jsx'
import ledgerlineLogoWhite from './assets/ledgerline_logo_white.png'
import './audit.css'
import './platform.css'

const pages = [['overview', 'Overview'], ['companies', 'Companies'], ['activity', 'Platform Activity'], ['health', 'System Health']]
const date = (value) => value ? new Date(value).toLocaleDateString() : '—'

function PlatformIcon({ name }) {
  const paths = {
    overview: <><path d="m3 11 9-7 9 7" /><path d="M5 10v10h14V10M10 20v-6h4v6" /></>,
    companies: <><rect x="5" y="3" width="12" height="18" rx="1" /><path d="M9 7h1m3 0h1M9 11h1m3 0h1M9 15h1m3 0h1M17 9h2v12H3" /></>,
    activity: <><path d="M4 20V10m5 10V6m5 14v-7m5 7V3M2 20h20" /></>,
    health: <path d="M3 12h4l2-7 4 14 2-7h6" />,
    active: <><circle cx="12" cy="12" r="9" /><path d="m8 12 3 3 5-6" /></>,
    documents: <><path d="M6 3h8l4 4v14H6z" /><path d="M14 3v5h4M9 12h6M9 16h6" /></>,
    audits: <><path d="m12 3 9 5-9 5-9-5 9-5ZM3 12l9 5 9-5M3 16l9 5 9-5" /></>,
  }
  return <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>
}

async function request(apiFetch, url, options) {
  const response = await apiFetch(url, options)
  let body
  try { body = await response.json() } catch { throw new Error(`Invalid server response (${response.status}).`) }
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}).`)
  return body
}

function Activity({ items }) {
  return items.length ? <div className="platform-activity">{items.map((item, index) =>
    <div key={`${item.business_id}-${item.occurred_at}-${index}`}><span className="platform-activity-icon"><PlatformIcon name={item.kind.includes('Company') ? 'companies' : item.kind.includes('Audit') ? 'active' : 'documents'} /></span><span className="platform-activity-copy"><strong>{item.kind}</strong><small>{item.company}</small></span><time>{date(item.occurred_at)}</time></div>
  )}</div> : <p>No recent activity.</p>
}

function CompanyTable({ companies, onOpen, compact = false }) {
  return <div className="audit-table-wrap"><table className={`audit-table ${compact ? 'platform-table-compact' : ''}`}>
    <thead><tr><th>Company</th><th>Administrator</th>{!compact && <th>Registered</th>}<th>Documents</th>{!compact && <th>Audit runs</th>}<th>Status</th><th><span className="platform-visually-hidden">Actions</span></th></tr></thead>
    <tbody>{companies.map((item) => <tr key={item.business_id}>
      <td><span className="platform-company-name"><span className="platform-company-icon"><PlatformIcon name="companies" /></span><span>{item.business_name}{compact && <small>Registered {date(item.created_at)}</small>}</span></span></td>
      <td>{item.admin_username || item.admin_email || (item.reserved_username ? `${item.reserved_username} (setup incomplete)` : 'Not created')}</td>{!compact && <td>{date(item.created_at)}</td>}
      <td>{item.document_count}</td>{!compact && <td>{item.audit_run_count}</td>}<td><span className={`platform-status is-${item.status}`}>{item.status}</span></td>
      <td><button className="platform-table-link" onClick={() => onOpen(`companies/${item.business_id}`)}>View <span className="platform-visually-hidden">{item.business_name}</span></button></td>
    </tr>)}</tbody>
  </table>{companies.length === 0 && <p>No companies registered yet.</p>}</div>
}

function Metrics({ items }) {
  return <div className="platform-metrics">{items.map(({ label, value, icon, detail }) => <div className="platform-metric" key={label}>
    <span className="platform-metric-icon"><PlatformIcon name={icon} /></span>
    <div><strong>{value.toLocaleString()}</strong><span>{label}</span>{detail && <small>{detail}</small>}</div>
  </div>)}</div>
}

export default function PlatformDashboard() {
  const { apiFetch, profile, signOut, setUsername: setOwnUsername } = useAuth()
  const [route, setRoute] = useState(() => window.location.hash.slice(1) || 'overview')
  const [companies, setCompanies] = useState(null)
  const [activity, setActivity] = useState(null)
  const [companyActivity, setCompanyActivity] = useState(null)
  const [health, setHealth] = useState(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [gstin, setGstin] = useState('')
  const [ownUsername, setOwnUsernameValue] = useState('')

  useEffect(() => {
    const change = () => setRoute(window.location.hash.slice(1) || 'overview')
    window.addEventListener('hashchange', change)
    return () => window.removeEventListener('hashchange', change)
  }, [])
  const navigate = (next) => { window.location.hash = next }
  const load = useCallback(async () => {
    setError('')
    try {
      const [companyData, activityData, healthData] = await Promise.all([
        request(apiFetch, '/api/admin/companies'),
        request(apiFetch, '/api/admin/platform/activity'),
        request(apiFetch, '/api/admin/platform/health'),
      ])
      if (!Array.isArray(companyData.companies) || !Array.isArray(activityData.activity)) throw new Error('The platform returned an invalid response.')
      setCompanies(companyData.companies)
      setActivity(activityData.activity)
      setHealth(healthData)
    } catch (err) {
      setCompanies(null)
      setActivity(null)
      setHealth(null)
      setError(err.message)
    }
  }, [apiFetch])
  useEffect(() => { load() }, [load])

  async function mutate(url, method, payload) {
    setBusy(true)
    setError('')
    setNotice('')
    try {
      const result = await request(apiFetch, url, {
        method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      setNotice(result.message || 'Company status updated.')
      await load()
      return true
    } catch (err) {
      await load()
      setError(err.message)
      return false
    } finally { setBusy(false) }
  }

  const section = route.split('/')[0]
  const page = pages.some(([key]) => key === section) ? section : 'overview'
  const companyId = page === 'companies' ? route.split('/')[1] : null
  useEffect(() => {
    if (!companyId) { setCompanyActivity(null); return undefined }
    let active = true
    setCompanyActivity(null)
    request(apiFetch, `/api/admin/platform/activity?business_id=${encodeURIComponent(companyId)}`)
      .then((body) => {
        if (!Array.isArray(body.activity)) throw new Error('The platform returned invalid company activity.')
        if (active) setCompanyActivity(body.activity)
      })
      .catch((err) => { if (active) setError(err.message) })
    return () => { active = false }
  }, [apiFetch, companyId, companies])
  const company = companies?.find((item) => item.business_id === companyId)
  useEffect(() => {
    setUsername(company?.reserved_username || '')
    setPassword('')
    setGstin('')
  }, [companyId, company?.reserved_username])
  const total = (key) => companies.reduce((sum, item) => sum + item[key], 0)
  const metrics = companies && [
    { label: 'Registered companies', value: companies.length, icon: 'companies', detail: `${companies.filter((item) => item.status === 'suspended').length} suspended` },
    { label: 'Active companies', value: companies.filter((item) => item.status === 'active').length, icon: 'active' },
    { label: 'Uploaded documents', value: total('document_count'), icon: 'documents', detail: `${total('processed_count').toLocaleString()} processed` },
    { label: 'Audit runs', value: total('audit_run_count'), icon: 'audits' },
  ]
  const hour = new Date().getHours()
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'

  return <div className="app-shell theme-light platform-shell">
    <aside className="sidebar platform-sidebar">
      <div className="brand-row"><div className="brand"><img src={ledgerlineLogoWhite} alt="Ledgerline" style={{ height: '32px', width: 'auto', display: 'block' }} /></div></div>
      <div className="platform-admin-label"><span aria-hidden="true">✦</span> Platform Admin</div>
      <div className="sidebar-section">
        <nav className="workspace-nav" aria-label="Platform navigation">{pages.map(([key, label]) =>
          <button key={key} className={`workspace-nav-item ${page === key ? 'is-selected' : ''}`} onClick={() => navigate(key)}><PlatformIcon name={key} />{label}</button>
        )}</nav>
      </div>
      <div className="sidebar-footer"><div className="profile-row"><span className="profile-avatar">{(profile.username || profile.email || 'S')[0].toUpperCase()}</span><span className="profile-copy"><strong>{profile.username || profile.email}</strong><small>Super Admin</small></span><button className="profile-more" onClick={signOut} aria-label="Sign out">↪</button></div></div>
    </aside>
    <main className="main-panel"><div className="audit-workspace platform-content">
      <header className="audit-page-header"><div><div className="audit-kicker">{page === 'overview' ? 'PLATFORM OVERVIEW' : 'LEDGERLINE PLATFORM'}</div><h1>{page === 'overview' ? `${greeting}, Super Admin` : company ? company.business_name : pages.find(([key]) => key === page)[1]}</h1><p>{page === 'overview' ? "Here's what's happening across the Ledgerline platform." : 'Platform management and monitoring'}</p></div><button className="audit-button secondary" onClick={load}>Refresh</button></header>
      {error && <div className="audit-alert error" role="alert">{error}</div>}
      {notice && <div className="audit-alert success" role="status">{notice}</div>}
      {!profile.username && <section className="audit-panel"><h2>Choose your username</h2><p>Your existing email sign-in will continue to work.</p>
        <form className="platform-form" onSubmit={async (event) => {
          event.preventDefault(); setBusy(true); setError('')
          try { await setOwnUsername(ownUsername); setNotice('Username saved.') } catch (err) { setError(err.message) } finally { setBusy(false) }
        }}><label>Username<input required minLength={3} maxLength={48} pattern="[A-Za-z0-9](?:[A-Za-z0-9_]|-)*" value={ownUsername} onChange={(event) => setOwnUsernameValue(event.target.value)} /></label><button className="audit-button primary" disabled={busy}>Save username</button></form>
      </section>}
      {!companies && !error && <p role="status">Loading platform data…</p>}
      {companies && page === 'overview' && <>
        <Metrics items={metrics} />
        <div className="platform-overview-grid">
          <section className="audit-panel platform-companies-panel"><div className="audit-section-heading"><h2>Registered companies</h2><button className="audit-button primary" onClick={() => navigate('companies')}>+ Register company</button></div>
            <CompanyTable companies={companies.slice(0, 5)} onOpen={navigate} compact />
            <div className="platform-panel-footer"><span>Showing {Math.min(companies.length, 5)} of {companies.length} companies</span><button className="platform-text-link" onClick={() => navigate('companies')}>View all companies <span aria-hidden="true">→</span></button></div>
          </section>
          <section className="audit-panel platform-activity-panel"><h2>Recent platform activity</h2><Activity items={activity.slice(0, 5)} /><button className="platform-activity-link" onClick={() => navigate('activity')}>View all activity <span aria-hidden="true">→</span></button></section>
        </div>
      </>}
      {companies && page === 'companies' && !companyId && <>
        <section className="audit-panel"><h2>Register a company</h2><p>Create a username and password for its Company Admin. Share the credentials securely.</p>
          {companies.some((item) => !item.registration_gstin) && <p>Verify the GSTIN for each existing company before registering another.</p>}
          <form className="platform-form" onSubmit={async (event) => { event.preventDefault(); const ok = await mutate('/api/admin/companies', 'POST', { company_name: name.trim(), gstin: gstin.trim(), username: username.trim(), password }); setPassword(''); if (ok) { setName(''); setGstin(''); setUsername('') } }}>
            <label>Company name<input required minLength={2} maxLength={120} value={name} onChange={(event) => setName(event.target.value)} /></label>
            <label>GSTIN<input required minLength={15} maxLength={15} pattern="[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]" value={gstin} onChange={(event) => setGstin(event.target.value.toUpperCase())} /></label>
            <label>Administrator username<input required minLength={3} maxLength={48} pattern="[A-Za-z0-9](?:[A-Za-z0-9_]|-)*" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
            <label>Administrator password<input required type="password" autoComplete="new-password" minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            <button className="audit-button primary" disabled={busy || companies.some((item) => !item.registration_gstin)}>Create company and account</button>
          </form>
        </section><section className="audit-panel"><h2>Companies</h2><CompanyTable companies={companies} onOpen={navigate} /></section>
      </>}
      {companies && page === 'companies' && companyId && (company ? <>
        <button className="audit-button secondary platform-back" onClick={() => navigate('companies')}>← All companies</button>
        <section className="audit-panel"><div className="audit-section-heading"><div><h2>Company details</h2><p>Read-only usage and activity</p></div>
          <button disabled={busy} className="audit-button secondary" onClick={() => mutate(`/api/admin/companies/${company.business_id}/status`, 'PATCH', { status: company.status === 'active' ? 'suspended' : 'active' })}>{company.status === 'active' ? 'Suspend company' : 'Reactivate company'}</button>
        </div><dl><div><dt>Administrator</dt><dd>{company.admin_username || company.admin_email || company.reserved_username || 'Not created'}</dd></div><div><dt>GSTIN</dt><dd>{company.registration_gstin || 'Verification required'}</dd></div><div><dt>Registered</dt><dd>{date(company.created_at)}</dd></div><div><dt>Status</dt><dd>{company.status}</dd></div></dl>
          {!company.registration_gstin && <form className="platform-form" onSubmit={async (event) => { event.preventDefault(); if (await mutate(`/api/admin/companies/${company.business_id}/registration`, 'PUT', { gstin: gstin.trim() })) setGstin('') }}><label>Verified company GSTIN<input required minLength={15} maxLength={15} pattern="[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]" value={gstin} onChange={(event) => setGstin(event.target.value.toUpperCase())} /></label><button className="audit-button primary" disabled={busy}>Verify GSTIN</button></form>}
          {!company.admin_user_id && company.account_status !== 'creating' && <form className="platform-form" onSubmit={async (event) => { event.preventDefault(); const ok = await mutate(`/api/admin/companies/${company.business_id}/administrator`, 'POST', { username: username.trim(), password }); setPassword(''); if (ok) setUsername('') }}>
            <label>Administrator username<input required minLength={3} maxLength={48} pattern="[A-Za-z0-9](?:[A-Za-z0-9_]|-)*" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
            <label>Administrator password<input required type="password" autoComplete="new-password" minLength={8} maxLength={128} value={password} onChange={(event) => setPassword(event.target.value)} /></label>
            <button className="audit-button primary" disabled={busy}>{company.reserved_username ? 'Retry account setup' : 'Create Company Admin'}</button>
          </form>}
          {company.account_status === 'creating' && <p>Account setup is in progress. If it was interrupted, contact the platform operator.</p>}
        </section>
        <Metrics items={[
          { label: 'Documents', value: company.document_count, icon: 'documents' },
          { label: 'Processed', value: company.processed_count, icon: 'active' },
          { label: 'Processing failed', value: company.failed_count, icon: 'health' },
          { label: 'Audit runs', value: company.audit_run_count, icon: 'audits' },
        ]} />
        <section className="audit-panel"><h2>Recent activity</h2>{companyActivity ? <Activity items={companyActivity} /> : error ? <p>Company activity is unavailable.</p> : <p>Loading activity…</p>}</section>
      </> : <div className="audit-alert error">Company not found. <button className="audit-button secondary" onClick={() => navigate('companies')}>All companies</button></div>)}
      {companies && page === 'activity' && <section className="audit-panel"><h2>Recent platform activity</h2><Activity items={activity} /></section>}
      {companies && page === 'health' && <section className="audit-panel"><h2>System status</h2><p>Database: {health.database === 'available' ? 'Available' : 'Unavailable'}</p><p>Documents with processing errors: {total('failed_count')}</p></section>}
    </div></main>
  </div>
}
