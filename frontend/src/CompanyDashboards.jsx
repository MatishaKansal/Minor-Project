import { useCallback, useEffect, useState } from 'react'
import { useAuth } from './auth/AuthContext.jsx'

export function PlatformDashboard() {
  const { apiFetch } = useAuth()
  const [companies, setCompanies] = useState([])
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const response = await apiFetch('/api/admin/companies')
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || `Could not load companies (${response.status}).`)
      setCompanies(body.companies || [])
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }, [apiFetch])

  useEffect(() => { load() }, [load])

  async function setStatus(company, status) {
    setBusyId(company.business_id)
    setError('')
    try {
      const response = await apiFetch(`/api/admin/companies/${company.business_id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status }),
      })
      const body = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(body.detail || `Could not update company (${response.status}).`)
      setCompanies((current) => current.map((item) => item.business_id === company.business_id ? { ...item, status } : item))
    } catch (updateError) {
      setError(updateError.message)
    } finally {
      setBusyId('')
    }
  }

  const active = companies.filter((company) => company.status === 'active').length
  return (
    <section className="audit-panel">
      <div className="audit-section-heading"><div><h2>Platform overview</h2><p>Companies on the platform. Financial records stay inside each company.</p></div><button className="audit-button secondary" onClick={load}>Refresh</button></div>
      <div className="audit-metrics">
        <div><strong>{companies.length}</strong><span>Companies</span></div>
        <div><strong>{active}</strong><span>Active</span></div>
        <div><strong>{companies.length - active}</strong><span>Suspended</span></div>
      </div>
      {error && <div className="audit-alert error" role="alert">{error}</div>}
      {loading ? <div className="audit-empty small-empty">Loading companies…</div> : companies.length === 0 ? <div className="audit-empty small-empty">No companies yet.</div> : (
        <div className="audit-table-wrap"><table className="audit-table"><thead><tr><th>Company</th><th>Company Admin</th><th>Users</th><th>Documents</th><th>Invoices</th><th>Status</th></tr></thead><tbody>
          {companies.map((company) => <tr key={company.business_id}>
            <td>{company.business_name || 'Unnamed company'}<small>{company.business_id}</small></td>
            <td>{company.admin_email || '—'}</td>
            <td>{company.user_count}</td>
            <td>{company.document_count}</td>
            <td>{company.invoice_count}</td>
            <td>{company.status}{' '}
              <button className="audit-button secondary" disabled={busyId === company.business_id} onClick={() => setStatus(company, company.status === 'active' ? 'suspended' : 'active')}>
                {company.status === 'active' ? 'Suspend' : 'Reactivate'}
              </button></td>
          </tr>)}
        </tbody></table></div>
      )}
    </section>
  )
}

export function CompanyOverview() {
  const { apiFetch } = useAuth()
  const [overview, setOverview] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    apiFetch('/api/company/overview')
      .then(async (response) => {
        const body = await response.json().catch(() => ({}))
        if (!response.ok) throw new Error(body.detail || `Could not load overview (${response.status}).`)
        if (active) setOverview(body)
      })
      .catch((loadError) => { if (active) setError(loadError.message) })
    return () => { active = false }
  }, [apiFetch])

  if (error) return <div className="audit-alert error" role="alert">{error}</div>
  if (!overview) return null
  return (
    <section className="audit-panel">
      <div className="audit-section-heading"><div><h2>{overview.company.business_name || 'Your company'}</h2><p>Upload documents, run the audit and review findings.</p></div></div>
      <div className="audit-metrics">
        <div><strong>{overview.documents}</strong><span>Documents</span></div>
        <div><strong>{overview.invoices}</strong><span>Invoices</span></div>
        <div><strong>{overview.engagements}</strong><span>Audits</span></div>
      </div>
    </section>
  )
}
