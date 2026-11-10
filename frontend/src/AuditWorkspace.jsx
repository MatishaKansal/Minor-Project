import { useCallback, useEffect, useState } from 'react'
import { useAuth } from './auth/AuthContext.jsx'
import './audit.css'

const emptySummary = {
  engagement_count: 0,
  active_engagement_count: 0,
  transaction_count: 0,
  transaction_value: '0',
  transaction_value_complete: true,
  transaction_currency: 'INR',
  tested_transaction_count: 0,
  coverage_percent: 0,
  open_findings: 0,
  high_risk_findings: 0,
  recent_findings: [],
  engagements: [],
}

const money = (value, currency = 'INR') => {
  if (value === null || value === undefined || value === '') return 'Not available'
  const match = /^([+-]?)(\d+)(?:\.(\d+))?$/.exec(String(value).trim())
  if (!match) return 'Not available'
  let whole = BigInt(match[2])
  let minor = BigInt((match[3] || '').padEnd(2, '0').slice(0, 2))
  if ((match[3] || '').length > 2 && match[3][2] >= '5') minor += 1n
  if (minor >= 100n) {
    whole += 1n
    minor -= 100n
  }
  try {
    const groupedWhole = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(whole)
    const formattedMinor = new Intl.NumberFormat(undefined, { minimumIntegerDigits: 2, useGrouping: false }).format(Number(minor))
    const parts = new Intl.NumberFormat(undefined, { style: 'currency', currency, minimumFractionDigits: 2, maximumFractionDigits: 2 }).formatToParts(match[1] === '-' ? -0 : 0)
    return parts.map((part) => {
      if (part.type === 'integer') return groupedWhole
      if (part.type === 'fraction') return formattedMinor
      if (part.type === 'group') return ''
      return part.value
    }).join('')
  } catch {
    return `${currency} ${match[1]}${whole}.${minor.toString().padStart(2, '0')}`
  }
}

function AuditWorkspace({ page, role, onOpenSearch }) {
  const { apiFetch } = useAuth()
  const canAudit = role === 'administrator' || role === 'auditor'
  const canReview = role === 'administrator' || role === 'reviewer'
  const [summary, setSummary] = useState(emptySummary)
  const [invoices, setInvoices] = useState([])
  const [detail, setDetail] = useState(null)
  const [activeTab, setActiveTab] = useState('Overview')
  const [selectedInvoices, setSelectedInvoices] = useState([])
  const [showInvoicePicker, setShowInvoicePicker] = useState(false)
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [form, setForm] = useState({
    entity_name: '',
    period_start: '',
    period_end: '',
    financial_year: '',
    audit_type: 'Financial statement audit',
    audit_scope: '',
    materiality_threshold: '',
    reporting_currency: 'INR',
    assigned_auditor: '',
  })
  const [reviewComment, setReviewComment] = useState({})
  const [statusComment, setStatusComment] = useState('')
  const [loading, setLoading] = useState(true)
  const [summaryReady, setSummaryReady] = useState(false)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  const request = useCallback(async (url, options) => {
    const response = await apiFetch(url, options)
    const body = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(body.detail || `The audit service returned ${response.status}.`)
    return body
  }, [apiFetch])

  const loadSummary = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await request('/api/audit/summary')
      setSummary({ ...emptySummary, ...data })
      setSummaryReady(true)
    } catch (loadError) {
      setSummaryReady(false)
      setError(loadError.message)
    } finally {
      setLoading(false)
    }
  }, [request])

  const loadEngagement = useCallback(async (engagementId) => {
    setWorking(true)
    setError('')
    try {
      const data = await request(`/api/audit/engagements/${engagementId}`)
      setDetail(data)
      setActiveTab('Overview')
    } catch (loadError) {
      setError(loadError.message)
    } finally {
      setWorking(false)
    }
  }, [request])

  useEffect(() => {
    loadSummary()
  }, [loadSummary])

  useEffect(() => {
    setDetail(null)
    setActiveTab('Overview')
  }, [page])

  useEffect(() => {
    if (!notice) return undefined
    const timeout = window.setTimeout(() => setNotice(''), 3500)
    return () => window.clearTimeout(timeout)
  }, [notice])

  async function createEngagement(event) {
    event.preventDefault()
    setWorking(true)
    setError('')
    try {
      const data = await request('/api/audit/engagements', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...form, materiality_threshold: Number(form.materiality_threshold) }),
      })
      setShowCreateForm(false)
      setForm({ entity_name: '', period_start: '', period_end: '', financial_year: '', audit_type: 'Financial statement audit', audit_scope: '', materiality_threshold: '', reporting_currency: 'INR', assigned_auditor: '' })
      setNotice('Engagement created.')
      await loadSummary()
      await loadEngagement(data.engagement.engagement_id)
    } catch (createError) {
      setError(createError.message)
    } finally {
      setWorking(false)
    }
  }

  async function openInvoicePicker() {
    setError('')
    try {
      const data = await request('/api/audit/invoices')
      const linked = new Set((detail?.invoices || []).map((invoice) => invoice.invoice_id))
      setInvoices(data.invoices.filter((invoice) => !linked.has(invoice.invoice_id)))
      setSelectedInvoices([])
      setShowInvoicePicker(true)
    } catch (loadError) {
      setError(loadError.message)
    }
  }

  async function linkInvoices() {
    if (!detail || selectedInvoices.length === 0) return
    setWorking(true)
    setError('')
    try {
      await request(`/api/audit/engagements/${detail.engagement.engagement_id}/transactions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ invoice_ids: selectedInvoices }),
      })
      setShowInvoicePicker(false)
      setNotice(`${selectedInvoices.length} invoice${selectedInvoices.length === 1 ? '' : 's'} linked to this engagement.`)
      await loadEngagement(detail.engagement.engagement_id)
      await loadSummary()
    } catch (linkError) {
      setError(linkError.message)
    } finally {
      setWorking(false)
    }
  }

  async function runProcedures() {
    if (!detail) return
    setWorking(true)
    setError('')
    try {
      const data = await request(`/api/audit/engagements/${detail.engagement.engagement_id}/run`, { method: 'POST' })
      setNotice(`Testing complete: ${data.outcomes.EXCEPTION || 0} exceptions, ${data.outcomes['INSUFFICIENT EVIDENCE'] || 0} procedures without sufficient evidence.`)
      await loadEngagement(detail.engagement.engagement_id)
      await loadSummary()
      setActiveTab('Procedures')
    } catch (runError) {
      setError(runError.message)
    } finally {
      setWorking(false)
    }
  }

  async function changeEngagementStatus(status) {
    if (!detail || !statusComment.trim()) {
      setError('Add a status-change note before changing the engagement status.')
      return
    }
    setWorking(true)
    setError('')
    try {
      await request(`/api/audit/engagements/${detail.engagement.engagement_id}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, comment: statusComment.trim() }),
      })
      setStatusComment('')
      setNotice(`Engagement moved to ${status}.`)
      await loadEngagement(detail.engagement.engagement_id)
      await loadSummary()
    } catch (statusError) {
      setError(statusError.message)
    } finally {
      setWorking(false)
    }
  }

  async function decideFinding(findingId, status) {
    const comment = (reviewComment[findingId] || '').trim()
    if (!comment) {
      setError('Add a review comment before changing a finding status.')
      return
    }
    setWorking(true)
    setError('')
    try {
      await request(`/api/audit/findings/${findingId}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, comment }),
      })
      setReviewComment((current) => ({ ...current, [findingId]: '' }))
      setNotice(`Finding marked ${status.toLowerCase()}.`)
      await loadSummary()
      if (detail) await loadEngagement(detail.engagement.engagement_id)
    } catch (decisionError) {
      setError(decisionError.message)
    } finally {
      setWorking(false)
    }
  }

  const title = page === 'engagements' ? 'Audit engagements' : page === 'findings' ? 'Audit findings' : 'Audit dashboard'
  const findingList = detail ? detail.findings : summary.recent_findings
  const statusActions = detail ? ({
    Draft: ['Planning'],
    Planning: ['In Progress'],
    'In Progress': ['Under Review'],
    'Under Review': [...(canAudit ? ['In Progress'] : []), ...(canReview ? ['Completed'] : [])],
    Completed: role === 'administrator' ? ['Archived'] : [],
    Archived: [],
  }[detail.engagement.status] || []) : []

  return (
    <div className="audit-workspace">
      <header className="audit-page-header">
        <div>
          <div className="audit-kicker">FINANCIAL AUDIT WORKSPACE</div>
          <h1>{detail ? detail.engagement.entity_name : title}</h1>
          <p>{detail ? `${detail.engagement.financial_year} · ${detail.engagement.period_start} to ${detail.engagement.period_end}` : 'Evidence-linked procedures, persistent findings, and engagement progress.'}</p>
        </div>
        <div className="audit-header-actions">
          {detail && <button className="audit-button secondary" onClick={() => { setDetail(null); setActiveTab('Overview') }}>All engagements</button>}
          {!detail && page !== 'findings' && canAudit && <button className="audit-button primary" onClick={() => setShowCreateForm(true)}>New engagement</button>}
          <button className="audit-button secondary" onClick={onOpenSearch}>Document search</button>
        </div>
      </header>

      <nav className="audit-tabs" aria-label="Audit navigation">
        {['dashboard', 'engagements', 'findings'].map((tab) => (
          <button key={tab} className={!detail && page === tab ? 'is-active' : ''} onClick={() => { setDetail(null); window.dispatchEvent(new CustomEvent('audit-page', { detail: tab })) }}>
            {tab === 'dashboard' ? 'Dashboard' : tab === 'engagements' ? 'Engagements' : 'Findings'}
          </button>
        ))}
        {detail && <span className="audit-tab-context">{detail.engagement.engagement_id.slice(0, 8).toUpperCase()}</span>}
      </nav>

      {error && <div className="audit-alert error" role="alert"><strong>Audit service unavailable</strong><span>{error}</span><button onClick={() => { setError(''); loadSummary() }}>Retry</button></div>}
      {notice && <div className="audit-alert success" role="status">{notice}</div>}

      {loading ? <div className="audit-empty">Loading audit records…</div> : !summaryReady && !detail ? (
        <div className="audit-empty"><strong>Live audit records are unavailable.</strong><span>Dashboard metrics are hidden until the audit API returns real engagement data.</span></div>
      ) : (
        detail ? (
          <section className="audit-engagement-view">
            <div className="audit-engagement-meta">
              <span className={`audit-status status-${detail.engagement.status.toLowerCase().replaceAll(' ', '-')}`}>{detail.engagement.status}</span>
              <span>{detail.engagement.audit_type}</span>
              <span>Materiality {money(detail.engagement.materiality_threshold, detail.engagement.reporting_currency)}</span>
              <span>Auditor: {detail.engagement.assigned_auditor}</span>
            </div>
            <nav className="audit-tabs inner-tabs" aria-label="Engagement sections">
              {['Overview', 'Transactions', 'Procedures', 'Findings'].map((tab) => <button key={tab} className={activeTab === tab ? 'is-active' : ''} onClick={() => setActiveTab(tab)}>{tab}</button>)}
            </nav>
            {activeTab === 'Overview' && <div className="audit-overview-grid">
              <section className="audit-panel"><h2>Engagement scope</h2><p>{detail.engagement.audit_scope}</p><dl><div><dt>Financial year</dt><dd>{detail.engagement.financial_year}</dd></div><div><dt>Reporting currency</dt><dd>{detail.engagement.reporting_currency}</dd></div><div><dt>Transactions linked</dt><dd>{detail.invoices.length}</dd></div><div><dt>Findings on record</dt><dd>{detail.findings.length}</dd></div></dl>
                {statusActions.length > 0 && <div className="status-transition"><input value={statusComment} onChange={(event) => setStatusComment(event.target.value)} aria-label="Status change note" placeholder="Reason for status change" />{statusActions.map((status) => <button key={status} className="audit-button secondary" disabled={working || !statusComment.trim()} onClick={() => changeEngagementStatus(status)}>Move to {status}</button>)}</div>}
              </section>
              <section className="audit-panel"><h2>Current audit position</h2><p>Run the available deterministic procedures against the linked invoice population. Unsupported procedures will be recorded as insufficient evidence, not passed.</p><div className="audit-action-row">{canAudit && <><button className="audit-button primary" onClick={runProcedures} disabled={working || detail.invoices.length === 0}>{working ? 'Working…' : 'Run audit procedures'}</button><button className="audit-button secondary" onClick={openInvoicePicker}>Link invoices</button></>}</div>{detail.invoices.length === 0 && <p className="audit-inline-note">Link invoices to define the population before testing.</p>}</section>
              <section className="audit-panel wide-panel"><h2>Latest test run</h2>{detail.runs[0] ? <p>{new Date(detail.runs[0].created_at).toLocaleString()} · {detail.runs[0].population_count} transactions · {detail.runs[0].procedure_count} procedures</p> : <p>No audit procedure runs have been recorded for this engagement.</p>}</section>
            </div>}
            {activeTab === 'Transactions' && <section className="audit-panel">
              <div className="audit-section-heading"><div><h2>Transaction population</h2><p>Only persisted invoices linked to this engagement are included.</p></div>{canAudit && <button className="audit-button secondary" onClick={openInvoicePicker}>Link invoices</button>}</div>
              <div className="audit-table-wrap"><table className="audit-table"><thead><tr><th>Invoice</th><th>Vendor</th><th>Date</th><th>Amount</th><th>Evidence ref</th></tr></thead><tbody>
                {detail.invoices.map((invoice) => <tr key={invoice.invoice_id}><td>{invoice.invoice_number || 'Number not extracted'}<small>{invoice.invoice_id.slice(0, 8)}</small></td><td>{invoice.seller_name || 'Not extracted'}</td><td>{invoice.invoice_date || 'Not extracted'}</td><td>{money(invoice.invoice_total, invoice.currency || detail.engagement.reporting_currency)}</td><td>{invoice.file_id ? invoice.file_id.slice(0, 8) : 'No source file ID'}</td></tr>)}
                {!detail.invoices.length && <tr><td colSpan="5" className="table-empty">No invoices linked yet. Add persisted invoices from the evidence repository.</td></tr>}
              </tbody></table></div>
            </section>}
            {activeTab === 'Procedures' && <section className="audit-panel">
              <div className="audit-section-heading"><div><h2>Procedure results</h2><p>{detail.latest_results.length} results in the latest run. Exceptions are review items, not fraud determinations.</p></div>{canAudit && <button className="audit-button primary" onClick={runProcedures} disabled={working || !detail.invoices.length}>{working ? 'Running…' : 'Run procedures'}</button>}</div>
              <div className="audit-result-list">{detail.latest_results.map((result, index) => <article className="audit-result" key={`${result.invoice_id}-${result.rule_id}-${index}`}><div><strong>{result.rule_id} · {result.result_details.procedure}</strong><span>{result.result_details.explanation}</span><small>{result.invoice_id.slice(0, 8)} · {result.result_details.discrepancy ? `Discrepancy ${result.result_details.discrepancy}` : 'No discrepancy calculated'}</small></div><span className={`outcome outcome-${result.outcome.toLowerCase().replaceAll(' ', '-')}`}>{result.outcome}</span></article>)}
                {!detail.latest_results.length && <p className="table-empty">No procedure results yet. Run procedures to create persistent results.</p>}</div>
            </section>}
            {activeTab === 'Findings' && <FindingList findings={findingList} role={role} reviewComment={reviewComment} setReviewComment={setReviewComment} decideFinding={decideFinding} working={working} />}
          </section>
        ) : page === 'engagements' ? (
          <section className="audit-panel">
            <div className="audit-section-heading"><div><h2>Engagement register</h2><p>Open an engagement to define its population and review audit work.</p></div>{canAudit && <button className="audit-button primary" onClick={() => setShowCreateForm(true)}>New engagement</button>}</div>
            <EngagementList engagements={summary.engagements} onOpen={loadEngagement} />
          </section>
        ) : page === 'findings' ? (
          <FindingList findings={findingList} role={role} reviewComment={reviewComment} setReviewComment={setReviewComment} decideFinding={decideFinding} working={working} />
        ) : (
          <>
            <div className="audit-metrics">
              <Metric label="Active engagements" value={summary.active_engagement_count} detail={`${summary.engagement_count} total`} />
              <Metric label="Transactions in scope" value={summary.transaction_count} detail={summary.transaction_value_complete ? money(summary.transaction_value, summary.transaction_currency || 'INR') : 'Value unavailable: missing totals or mixed currencies'} />
              <Metric label="Test coverage" value={`${summary.coverage_percent}%`} detail={`${summary.tested_transaction_count} transactions tested`} />
              <Metric label="Open findings" value={summary.open_findings} detail={`${summary.high_risk_findings} high / critical`} alert={summary.high_risk_findings > 0} />
            </div>
            <div className="audit-dashboard-grid">
              <section className="audit-panel dashboard-engagements"><div className="audit-section-heading"><div><h2>Engagement progress</h2><p>Current audit assignments and populations.</p></div><button className="text-button" onClick={() => window.dispatchEvent(new CustomEvent('audit-page', { detail: 'engagements' }))}>View all</button></div><EngagementList engagements={summary.engagements.slice(0, 5)} onOpen={loadEngagement} compact /></section>
              <section className="audit-panel dashboard-findings"><div className="audit-section-heading"><div><h2>Recent findings</h2><p>Persistent exceptions requiring auditor review.</p></div><button className="text-button" onClick={() => window.dispatchEvent(new CustomEvent('audit-page', { detail: 'findings' }))}>View all</button></div><FindingList findings={summary.recent_findings.slice(0, 5)} role={role} reviewComment={reviewComment} setReviewComment={setReviewComment} decideFinding={decideFinding} working={working} compact /></section>
            </div>
            {summary.engagement_count === 0 && <div className="audit-empty-cta"><strong>No audit engagements yet</strong><span>Create an engagement to define an entity, financial period, audit scope, and materiality threshold.</span>            {canAudit && <button className="audit-button primary" onClick={() => setShowCreateForm(true)}>Create first engagement</button>}</div>}
          </>
        )
      )}

      {showCreateForm && <div className="audit-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowCreateForm(false) }}><form className="audit-modal" onSubmit={createEngagement}>
        <div className="audit-section-heading"><div><h2>New audit engagement</h2><p>Engagement records are stored in the configured Supabase business.</p></div><button type="button" className="modal-close" onClick={() => setShowCreateForm(false)} aria-label="Close">×</button></div>
        <label>Business / entity name<input required maxLength="200" value={form.entity_name} onChange={(event) => setForm({ ...form, entity_name: event.target.value })} /></label>
        <div className="audit-form-grid"><label>Period start<input type="date" required value={form.period_start} onChange={(event) => setForm({ ...form, period_start: event.target.value })} /></label><label>Period end<input type="date" required value={form.period_end} onChange={(event) => setForm({ ...form, period_end: event.target.value })} /></label></div>
        <div className="audit-form-grid"><label>Financial year<input required placeholder="2025-26" value={form.financial_year} onChange={(event) => setForm({ ...form, financial_year: event.target.value })} /></label><label>Audit type<input required value={form.audit_type} onChange={(event) => setForm({ ...form, audit_type: event.target.value })} /></label></div>
        <label>Audit scope<textarea required maxLength="4000" rows="3" value={form.audit_scope} onChange={(event) => setForm({ ...form, audit_scope: event.target.value })} placeholder="Areas and transaction categories included" /></label>
        <div className="audit-form-grid"><label>Materiality threshold<input type="number" min="0.01" step="0.01" required value={form.materiality_threshold} onChange={(event) => setForm({ ...form, materiality_threshold: event.target.value })} /></label><label>Reporting currency<input required minLength="3" maxLength="3" value={form.reporting_currency} onChange={(event) => setForm({ ...form, reporting_currency: event.target.value.toUpperCase() })} /></label></div>
        <label>Assigned auditor<input required value={form.assigned_auditor} onChange={(event) => setForm({ ...form, assigned_auditor: event.target.value })} /></label>
        <div className="audit-modal-actions"><button type="button" className="audit-button secondary" onClick={() => setShowCreateForm(false)}>Cancel</button><button className="audit-button primary" disabled={working}>{working ? 'Saving…' : 'Create engagement'}</button></div>
      </form></div>}

      {showInvoicePicker && <div className="audit-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowInvoicePicker(false) }}><section className="audit-modal">
        <div className="audit-section-heading"><div><h2>Link persisted invoices</h2><p>Only invoices from the active business are listed.</p></div><button type="button" className="modal-close" onClick={() => setShowInvoicePicker(false)} aria-label="Close">×</button></div>
        <div className="invoice-picker">{invoices.map((invoice) => <label key={invoice.invoice_id}><input type="checkbox" checked={selectedInvoices.includes(invoice.invoice_id)} onChange={(event) => setSelectedInvoices((current) => event.target.checked ? [...current, invoice.invoice_id] : current.filter((id) => id !== invoice.invoice_id))} /><span><strong>{invoice.invoice_number || 'Invoice number not extracted'}</strong><small>{invoice.seller_name || 'Vendor not extracted'} · {invoice.invoice_date || 'Date unavailable'} · {money(invoice.invoice_total, invoice.currency || 'INR')}</small></span></label>)}{invoices.length === 0 && <p className="table-empty">No unlinked persisted invoices are available. Upload and persist invoice evidence through document search first.</p>}</div>
        <div className="audit-modal-actions"><button className="audit-button secondary" onClick={() => setShowInvoicePicker(false)}>Cancel</button><button className="audit-button primary" disabled={working || !selectedInvoices.length} onClick={linkInvoices}>{working ? 'Linking…' : `Link ${selectedInvoices.length} invoice${selectedInvoices.length === 1 ? '' : 's'}`}</button></div>
      </section></div>}
    </div>
  )
}

function Metric({ label, value, detail, alert }) {
  return <section className={`audit-metric ${alert ? 'metric-alert' : ''}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></section>
}

function EngagementList({ engagements, onOpen, compact = false }) {
  if (!engagements.length) return <div className="audit-empty small-empty">No engagements found.</div>
  return <div className="engagement-list">{engagements.map((engagement) => <button className="engagement-row" key={engagement.engagement_id} onClick={() => onOpen(engagement.engagement_id)}>
    <span className="engagement-main"><strong>{engagement.entity_name}</strong><small>{engagement.financial_year} · {engagement.period_start} — {engagement.period_end}</small></span>
    {!compact && <span className="engagement-period">{engagement.audit_type}</span>}
    <span className={`audit-status status-${engagement.status.toLowerCase().replaceAll(' ', '-')}`}>{engagement.status}</span>
    <span className="engagement-arrow">›</span>
  </button>)}</div>
}

function FindingList({ findings, role, reviewComment, setReviewComment, decideFinding, working, compact = false }) {
  if (!findings.length) return <div className="audit-panel"><div className="audit-empty small-empty">No findings have been recorded. Run the available audit procedures to evaluate linked transactions.</div></div>
  return <section className={`audit-panel ${compact ? 'compact-findings' : ''}`}><div className="audit-finding-list">{findings.map((finding) => <article className="audit-finding" key={finding.finding_id}>
    <div className="finding-main"><div className="finding-title-row"><strong>{finding.title}</strong><span className={`severity severity-${finding.severity.toLowerCase()}`}>{finding.severity}</span><span className="finding-status">{finding.status}</span></div><p>{finding.description}</p><p className="finding-basis">{finding.severity_basis}{finding.financial_exposure !== null && finding.financial_exposure !== undefined ? ` Exposure: ${finding.financial_exposure}.` : ''}</p><small>{finding.rule_id} · Invoice {finding.invoice_id.slice(0, 8)} · {new Date(finding.created_at).toLocaleDateString()}</small></div>
    {!compact && role !== 'read_only_viewer' && <div className="finding-review"><input aria-label="Review comment" placeholder="Add review comment…" value={reviewComment[finding.finding_id] || ''} onChange={(event) => setReviewComment((current) => ({ ...current, [finding.finding_id]: event.target.value }))} /><div>{findingActions(finding.status, role).map((status) => <button key={status} disabled={working} onClick={() => decideFinding(finding.finding_id, status)}>{({ 'Under Review': 'Review', Confirmed: 'Confirm', Resolved: 'Resolve', 'False Positive': 'Dismiss', Closed: 'Close' })[status]}</button>)}</div></div>}
  </article>)}</div></section>
}

function findingActions(currentStatus, role) {
  const reviewer = role === 'administrator' || role === 'reviewer'
  const auditor = role === 'administrator' || role === 'auditor'
  const actions = {
    Open: ['Under Review', 'Confirmed', 'False Positive', 'Resolved', 'Closed'],
    'Under Review': ['Confirmed', 'False Positive', 'Resolved', 'Closed'],
    Confirmed: ['Resolved', 'Closed'],
    'False Positive': ['Closed'],
    Resolved: ['Closed'],
    Closed: [],
  }[currentStatus] || []
  return actions.filter((status) => {
    if (['Confirmed', 'False Positive', 'Closed'].includes(status)) return reviewer
    return auditor || role === 'reviewer'
  })
}

export default AuditWorkspace
