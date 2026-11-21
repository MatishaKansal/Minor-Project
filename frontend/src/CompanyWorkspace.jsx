import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from './auth/AuthContext.jsx'
import './company.css'

const money = (value, currency) => value === null || value === undefined || value === ''
  ? 'Not available'
  : `${currency || ''} ${value}`.trim()
const date = (value) => value ? new Date(value).toLocaleDateString() : 'Not available'

async function request(apiFetch, url, options) {
  const response = await apiFetch(url, options)
  let body
  try { body = await response.json() } catch { throw new Error(`Invalid response from the server (${response.status}).`) }
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}).`)
  return body
}

function Button({ children, onClick, secondary = false, disabled = false, type = 'button' }) {
  return <button type={type} className={`company-button ${secondary ? 'secondary' : ''}`} onClick={onClick} disabled={disabled}>{children}</button>
}

export default function CompanyWorkspace({ page, navigate, onAskDocument, openEmail, openPos, openMenu }) {
  const { apiFetch, profile } = useAuth()
  const [overview, setOverview] = useState(null)
  const [summary, setSummary] = useState(null)
  const [documents, setDocuments] = useState(null)
  const [invoices, setInvoices] = useState(null)
  const [detail, setDetail] = useState(null)
  const [selectedId, setSelectedId] = useState('')
  const [selectedInvoices, setSelectedInvoices] = useState([])
  const [patrol, setPatrol] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ start: '', end: '', threshold: '', currency: 'INR' })
  const [comments, setComments] = useState({})
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [working, setWorking] = useState(false)
  const [loading, setLoading] = useState(true)
  const [uploadFiles, setUploadFiles] = useState([])
  const [uploading, setUploading] = useState(false)
  const [documentReview, setDocumentReview] = useState(null)
  const uploadRef = useRef(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [company, checks, files, records] = await Promise.all([
        request(apiFetch, '/api/company/overview'),
        request(apiFetch, '/api/audit/summary'),
        request(apiFetch, '/api/company/documents'),
        request(apiFetch, '/api/audit/invoices'),
      ])
      if (!company.company || !Array.isArray(checks.engagements) || !Array.isArray(files.documents) || !Array.isArray(records.invoices)) {
        throw new Error('The server returned incomplete company information.')
      }
      setOverview(company)
      setSummary(checks)
      setDocuments(files)
      setInvoices(records.invoices)
      setSelectedId((current) => checks.engagements.some((item) => item.engagement_id === current) ? current : checks.engagements[0]?.engagement_id || '')
      return true
    } catch (err) {
      setError(err.message)
      setOverview(null)
      setSummary(null)
      setDocuments(null)
      setInvoices(null)
      return false
    } finally { setLoading(false) }
  }, [apiFetch])

  const loadDetail = useCallback(async (id) => {
    if (!id) { setDetail(null); return }
    setDetail(null)
    try {
      const result = await request(apiFetch, `/api/audit/engagements/${encodeURIComponent(id)}`)
      if (!result.engagement || !Array.isArray(result.invoices) || !Array.isArray(result.findings) || !Array.isArray(result.runs) || !Array.isArray(result.latest_results)) {
        throw new Error('The server returned incomplete check details.')
      }
      setDetail(result)
      return true
    } catch (err) { setError(err.message); return false }
  }, [apiFetch])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    let active = true
    if (!selectedId) { setDetail(null); return () => { active = false } }
    setDetail(null)
    request(apiFetch, `/api/audit/engagements/${encodeURIComponent(selectedId)}`)
      .then((result) => {
        if (!result.engagement || !Array.isArray(result.invoices) || !Array.isArray(result.findings) || !Array.isArray(result.runs) || !Array.isArray(result.latest_results)) throw new Error('The server returned incomplete check details.')
        if (active) setDetail(result)
      })
      .catch((err) => { if (active) setError(err.message) })
    return () => { active = false }
  }, [apiFetch, selectedId])

  async function perform(action, message, refreshDetail = true) {
    setWorking(true); setError(''); setNotice('')
    try {
      await action()
      if (!await load()) return
      if (refreshDetail && selectedId && !await loadDetail(selectedId)) return
      setNotice(message)
    } catch (err) { setError(err.message) } finally { setWorking(false) }
  }

  async function createCheck(event) {
    event.preventDefault()
    setWorking(true); setError(''); setNotice('')
    try {
      const result = await request(apiFetch, '/api/audit/engagements', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entity_name: overview.company.business_name, period_start: form.start, period_end: form.end,
          financial_year: `${form.start.slice(0, 4)}-${form.end.slice(0, 4)}`,
          audit_type: 'Financial checks', audit_scope: 'Review selected invoice records.',
          materiality_threshold: form.threshold, reporting_currency: form.currency,
          assigned_auditor: profile.username || profile.email || profile.user_id,
        }),
      })
      if (!result.engagement?.engagement_id) throw new Error('The server did not return the new check.')
      setShowCreate(false)
      if (!await load()) return
      setSelectedId(result.engagement.engagement_id)
      setNotice('Your review is ready. Select the records you want to check.')
    } catch (err) { setError(err.message) } finally { setWorking(false) }
  }

  async function matchRecords() {
    setWorking(true); setError(''); setNotice(''); setPatrol(null)
    try {
      const result = await request(apiFetch, '/api/actions/run-patrol', { method: 'POST' })
      if (!Array.isArray(result.anomalies) || typeof result.total_invoices_scanned !== 'number' || typeof result.total_bank_transactions !== 'number') throw new Error('The server returned incomplete matching results.')
      setPatrol(result)
    } catch (err) { setError(err.message) } finally { setWorking(false) }
  }

  async function downloadDocument(fileId, filename) {
    setError('')
    try {
      const response = await apiFetch(`/api/company/documents/${encodeURIComponent(fileId)}`)
      if (!response.ok) {
        const body = await response.json()
        throw new Error(typeof body.detail === 'string' ? body.detail : `Download failed (${response.status}).`)
      }
      const url = URL.createObjectURL(await response.blob())
      const link = document.createElement('a')
      link.href = url
      link.download = filename || 'document'
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) { setError(err.message) }
  }

  async function downloadLetter(invoiceNumber) {
    setError('')
    try {
      const url = `/api/actions/generate-letter-pdf${invoiceNumber ? `?invoice_number=${encodeURIComponent(invoiceNumber)}` : ''}`
      const response = await apiFetch(url)
      if (!response.ok) {
        const body = await response.json()
        throw new Error(typeof body.detail === 'string' ? body.detail : `Failed to generate letter (${response.status})`)
      }
      const blob = await response.blob()
      const downloadUrl = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = downloadUrl
      link.download = `Audit_Discrepancy_Notice_${invoiceNumber || 'Record'}.pdf`
      link.click()
      window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 1000)
    } catch (err) { setError(err.message) }
  }

  async function uploadDocuments() {
    setUploading(true); setError(''); setNotice('')
    for (let index = 0; index < uploadFiles.length; index += 1) {
      const file = uploadFiles[index].file
      const update = (status, error = '') => setUploadFiles((current) => current.map((item, i) => i === index ? { ...item, status, error } : item))
      try {
        update('Uploading')
        const body = new FormData()
        body.append('file', file, file.name)
        const result = await request(apiFetch, '/api/company/documents', { method: 'POST', body })
        if (!result.document?.file_id) throw new Error('The server did not confirm the document upload.')
        update('Processing')
        await request(apiFetch, `/api/company/documents/${encodeURIComponent(result.document.file_id)}/process`, { method: 'POST' })
        update('Extracted')
      } catch (err) {
        update('Failed', err.message)
      }
    }
    await load()
    setUploading(false)
  }

  async function retryDocument(fileId) {
    setWorking(true); setError(''); setNotice('')
    try {
      await request(apiFetch, `/api/company/documents/${encodeURIComponent(fileId)}/process`, { method: 'POST' })
      if (await load()) setNotice('Document processing finished.')
    } catch (err) { await load(); setError(err.message) } finally { setWorking(false) }
  }

  async function reviewDocument(fileId) {
    setWorking(true); setError(''); setDocumentReview(null)
    try {
      const result = await request(apiFetch, `/api/company/documents/${encodeURIComponent(fileId)}/review`)
      if (!result.document || !Array.isArray(result.invoices) || !Array.isArray(result.suspected_duplicates)) throw new Error('The server returned incomplete document details.')
      setDocumentReview(result)
    } catch (err) { setError(err.message) } finally { setWorking(false) }
  }

  const links = detail ? new Set(detail.invoices.map((item) => item.invoice_id)) : new Set()
  const available = invoices?.filter((item) => !links.has(item.invoice_id)) || []
  const next = !overview?.documents ? ['Upload your first records', 'documents']
    : !overview?.invoices ? ['Review your uploaded documents', 'documents']
      : !summary?.engagement_count ? ['Start a financial check', 'checks']
        : !summary?.completed_check_count ? ['Choose records and run your checks', 'checks']
          : summary.open_findings ? ['Review the items that need attention', 'checks']
            : ['Prepare your report', 'reports']
  const titles = { overview: 'Overview', documents: 'My Documents', transactions: 'Transactions', checks: 'Financial Checks', reports: 'Reports', reconciliation: 'Reconciliation', settings: 'Company Settings' }
  const chooseCheck = <label className="company-select">Financial review<select disabled={working} value={selectedId} onChange={(event) => { setSelectedId(event.target.value); setSelectedInvoices([]) }}><option value="">Select a review</option>{summary?.engagements.map((item) => <option key={item.engagement_id} value={item.engagement_id}>{item.entity_name} · {item.period_start} to {item.period_end}</option>)}</select></label>

  return <div className="company-content">
    <header className="company-header"><div><button className="company-menu" onClick={openMenu} aria-label="Open menu">☰</button><span className="company-kicker">YOUR FINANCIAL WORKSPACE</span><h1>{titles[page]}</h1><p>{page === 'overview' ? `A clear picture of ${overview?.company?.business_name || 'your company'}'s records.` : 'Your company’s records, all in one place.'}</p></div><Button secondary onClick={load} disabled={loading}>Refresh</Button></header>
    {error && <div className="company-alert" role="alert">{error}</div>}
    {notice && <div className="company-notice" role="status">{notice}</div>}
    {loading && !overview && <p role="status">Loading your financial records…</p>}
    {overview && summary && documents && invoices && <>
      {page === 'overview' && <>
        <div className="company-metrics">
          {[['Documents uploaded', overview.documents, 'Files saved to your company'], ['Transactions extracted', overview.invoices, 'Invoice records; bank entries not included'], ['Items needing attention', summary.open_findings, 'Open financial issues'], ['Financial checks completed', summary.completed_check_count, 'Checks with a definite result']].map(([label, value, note]) => <section className="company-metric" key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></section>)}
        </div>
        <div className="company-overview-grid">
          <section className="company-panel company-next"><span className="company-kicker">YOUR NEXT STEP</span><h2>{next[0]}</h2><p>Work through your records at your own pace. You can return to any step when you need to.</p><Button onClick={() => navigate(next[1])}>Continue <span aria-hidden="true">→</span></Button></section>
          <section className="company-panel"><h2>Quick actions</h2><div className="company-actions"><Button onClick={() => navigate('documents')}>Upload Documents</Button><Button secondary onClick={() => navigate('transactions')}>Check Transactions</Button><Button secondary onClick={() => navigate('checks')}>Review Financial Issues</Button><Button secondary onClick={() => navigate('reports')}>Generate Report</Button></div></section>
        </div>
        <section className="company-panel"><div className="company-section-heading"><div><h2>Your financial workflow</h2><p>Six straightforward steps to review your records.</p></div></div><div className="company-steps">{[
          ['Upload financial records', 'documents'], ['Review extracted information', 'transactions'], ['Match transactions with invoices', 'transactions'],
          ['Run financial checks', 'checks'], ['Review discrepancies and evidence', 'checks'], ['Generate your report', 'reports'],
        ].map(([label, target], index) => <button key={label} onClick={() => navigate(target)}><span>{index + 1}</span><strong>{label}</strong><b aria-hidden="true">→</b></button>)}</div></section>
      </>}

      {page === 'documents' && <section className="company-panel"><div className="company-section-heading"><div><h2>Your documents</h2><p>Upload invoices, receipts and other financial records to your company.</p></div><Button disabled={uploading} onClick={() => uploadRef.current?.click()}>Choose Documents</Button><input ref={uploadRef} hidden type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.txt,.xlsx,.xlsm,.wav,.mp3,.ogg,.flac,.aiff,.aif" onChange={(event) => { const files = Array.from(event.target.files || []); event.target.value = ''; if (files.length) setUploadFiles(files.map((file) => ({ file, status: 'Ready', error: '' }))) }} /></div>
        {uploadFiles.length > 0 && <div className="company-upload-list"><strong>Selected documents</strong>{uploadFiles.map((item, index) => <p key={`${item.file.name}-${index}`}>{item.file.name} — {item.status}{item.error && <span role="alert">: {item.error}</span>}</p>)}<div className="company-actions">{uploadFiles.every((item) => item.status === 'Ready') && <Button disabled={uploading} onClick={uploadDocuments}>Confirm upload</Button>}<Button secondary disabled={uploading} onClick={() => setUploadFiles([])}>Clear list</Button></div></div>}
        <p className="company-note">Showing up to 100 of {documents.total} saved documents.</p>
        <div className="company-table-wrap"><table className="company-table"><thead><tr><th>Document</th><th>Uploaded</th><th>Processing</th><th>Actions</th></tr></thead><tbody>{documents.documents.map((file) => <tr key={file.file_id}><td>{file.file_name}</td><td>{date(file.uploaded_at)}</td><td>{{ pending: 'Queued', processing: 'Processing', processed: 'Extracted', failed: 'Failed' }[file.processing_status] || file.processing_status || 'Unknown'}</td><td><button className="company-link" onClick={() => downloadDocument(file.file_id, file.file_name)}>Download</button>{file.processing_status === 'processed' && <> · <button className="company-link" disabled={working} onClick={() => reviewDocument(file.file_id)}>Review extraction</button></>}{['pending', 'failed'].includes(file.processing_status) && <> · <button className="company-link" disabled={working} onClick={() => retryDocument(file.file_id)}>Retry processing</button></>} · <button className="company-link" onClick={() => onAskDocument(file)}>Ask AI</button></td></tr>)}</tbody></table>{!documents.documents.length && <p className="company-empty">No documents yet. Upload your first records to get started.</p>}</div>
        {documentReview && <div className="company-panel"><div className="company-section-heading"><h2>Review: {documentReview.document.file_name}</h2><Button secondary onClick={() => setDocumentReview(null)}>Close</Button></div>
          <Button secondary onClick={() => downloadDocument(documentReview.document.file_id, documentReview.document.file_name)}>Download original</Button>
          {documentReview.suspected_duplicates.length > 0 && <p className="company-alert" role="alert">Possible duplicate: {documentReview.suspected_duplicates.length} other record(s) have the same supplier and invoice number. Compare the originals before making changes.</p>}
          {documentReview.suspected_duplicates.map((duplicate) => <p key={duplicate.invoice_id}>{duplicate.seller_name} · {duplicate.invoice_number}{duplicate.file_id && <> · <button className="company-link" onClick={() => reviewDocument(duplicate.file_id)}>Review matching document</button></>}</p>)}
          {!documentReview.invoices.length && <p>No extracted invoice records are available.</p>}
          {documentReview.invoices.map(({ record, extraction, provenance }) => <div key={record.invoice_id}>
            <table className="company-table"><tbody>{[['Invoice number', 'invoice_number'], ['Supplier', 'seller_name'], ['Invoice date', 'invoice_date'], ['Due date', 'due_date'], ['Total', 'invoice_total']].map(([label, key]) => <tr key={key}><th>{label}</th><td>{record[key] ?? 'Not extracted'}</td><td>{provenance?.find((item) => item.field_name === key)?.source_text || ''}</td></tr>)}</tbody></table>
            <details><summary>Extracted text</summary><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{extraction?.raw_text || 'No extracted text available.'}</pre></details>
          </div>)}
        </div>}
      </section>}

      {page === 'transactions' && <section className="company-panel"><div className="company-section-heading"><div><h2>Extracted invoice records</h2><p>Check that the information below matches your documents. Showing up to 500 records.</p></div><Button secondary onClick={() => navigate('documents')}>View documents</Button></div>
        <div className="company-table-wrap"><table className="company-table"><thead><tr><th>Invoice</th><th>Supplier</th><th>Date</th><th>Amount</th><th>Source</th></tr></thead><tbody>{invoices.map((item) => <tr key={item.invoice_id}><td>{item.invoice_number || 'Not extracted'}</td><td>{item.seller_name || 'Not extracted'}</td><td>{item.invoice_date || 'Not extracted'}</td><td>{money(item.invoice_total, item.currency)}</td><td>{item.file_id ? <button className="company-link" onClick={() => downloadDocument(item.file_id, documents.documents.find((file) => file.file_id === item.file_id)?.file_name)}>Download source</button> : 'Not available'}</td></tr>)}</tbody></table>{!invoices.length && <p className="company-empty">No invoice information has been extracted yet.</p>}</div>
      </section>}

      {page === 'checks' && <>
        <section className="company-panel"><div className="company-section-heading"><div><h2>Financial checks</h2><p>Choose a period, select invoice records, then run checks for duplicates, totals, tax and dates.</p></div><Button onClick={() => setShowCreate((current) => !current)}>{showCreate ? 'Cancel' : 'Start a review'}</Button></div>
          {showCreate && <form className="company-form" onSubmit={createCheck}>
            <label>Start date<input type="date" required value={form.start} onChange={(event) => setForm({ ...form, start: event.target.value })} /></label>
            <label>End date<input type="date" required min={form.start} value={form.end} onChange={(event) => setForm({ ...form, end: event.target.value })} /></label>
            <label>Amount you consider significant<input type="number" min="0.01" step="0.01" required value={form.threshold} onChange={(event) => setForm({ ...form, threshold: event.target.value })} /></label>
            <label>Currency<input required minLength={3} maxLength={3} pattern="[A-Za-z]{3}" value={form.currency} onChange={(event) => setForm({ ...form, currency: event.target.value.toUpperCase() })} /></label>
            <p className="company-note">The amount helps prioritize issues; it does not change the checks performed.</p><Button type="submit" disabled={working}>{working ? 'Creating…' : 'Create review'}</Button>
          </form>}
          {summary.engagements.length > 0 && chooseCheck}
          {!summary.engagements.length && !showCreate && <p className="company-empty">Start a review to choose which records to check.</p>}
        </section>
        {selectedId && !detail && !error && <p role="status">Loading review…</p>}
        {detail && <>
          <section className="company-panel"><div className="company-section-heading"><div><h2>Choose records</h2><p>{detail.invoices.length} invoice records selected for this review.</p></div><Button onClick={() => perform(() => request(apiFetch, `/api/audit/engagements/${selectedId}/run`, { method: 'POST' }), 'Check run finished. Some checks may need more information.')} disabled={working || !detail.invoices.length}>{working ? 'Working…' : 'Run financial checks'}</Button></div>
            {available.length > 0 && <><div className="company-pick-list">{available.map((item) => <label key={item.invoice_id}><input type="checkbox" checked={selectedInvoices.includes(item.invoice_id)} onChange={(event) => setSelectedInvoices((current) => event.target.checked ? [...current, item.invoice_id] : current.filter((id) => id !== item.invoice_id))} /><span><strong>{item.invoice_number || 'Invoice number unavailable'}</strong><small>{item.seller_name || 'Supplier unavailable'} · {money(item.invoice_total, item.currency)}</small></span></label>)}</div><Button secondary disabled={working || !selectedInvoices.length} onClick={() => perform(async () => { await request(apiFetch, `/api/audit/engagements/${selectedId}/transactions`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ invoice_ids: selectedInvoices }) }); setSelectedInvoices([]) }, 'Records added to your review.')}>Add selected records</Button></>}
            {!available.length && !detail.invoices.length && <p className="company-empty">Upload invoice documents before running checks.</p>}
          </section>
          <section className="company-panel"><h2>Items to review</h2><p>These are possible issues, not proof of wrongdoing. Check the supporting document before deciding.</p>
            {detail.findings.length ? detail.findings.map((item) => <article className="company-issue" key={item.finding_id}><div className="company-issue-heading"><strong>{item.title}</strong><span>{item.status}</span></div><p>{item.description}</p><small>Source: {documents.documents.find((file) => file.file_id === item.evidence_refs?.[0]?.file_id)?.file_name || 'See linked invoice record'} · {item.severity} priority</small>{item.evidence_refs?.[0]?.file_id && <div><button className="company-link" onClick={() => downloadDocument(item.evidence_refs[0].file_id, documents.documents.find((file) => file.file_id === item.evidence_refs[0].file_id)?.file_name)}>Download supporting document</button></div>}
              {!['Closed', 'Resolved', 'False Positive'].includes(item.status) && <div className="company-review"><input aria-label={`Note for ${item.title}`} placeholder="Add a note about your decision" value={comments[item.finding_id] || ''} onChange={(event) => setComments({ ...comments, [item.finding_id]: event.target.value })} /><Button secondary disabled={working || !(comments[item.finding_id] || '').trim()} onClick={() => perform(() => request(apiFetch, `/api/audit/findings/${item.finding_id}/decision`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'Resolved', comment: comments[item.finding_id].trim() }) }), 'Issue marked resolved.')}>Mark resolved</Button><Button secondary disabled={working || !(comments[item.finding_id] || '').trim() || item.status === 'Confirmed'} onClick={() => perform(() => request(apiFetch, `/api/audit/findings/${item.finding_id}/decision`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'False Positive', comment: comments[item.finding_id].trim() }) }), 'Issue dismissed.')}>Dismiss</Button></div>}
            </article>) : <p className="company-empty">{detail.runs.length ? 'No issues were recorded in this review.' : 'Run checks to see items that need attention.'}</p>}
            {detail.latest_results.some((item) => item.outcome === 'INSUFFICIENT EVIDENCE') && <p className="company-note">Some checks could not be completed because supporting information was unavailable. They are not counted as passed.</p>}
          </section>
        </>}
      </>}

      {page === 'reports' && <section className="company-panel"><div className="company-section-heading"><div><h2>Financial report</h2><p>Prepare a printable summary of a completed check run and its recorded issues.</p></div>{detail?.runs.length > 0 && <Button onClick={() => window.print()}>Print / Save PDF</Button>}</div>
        {summary.engagements.length ? chooseCheck : <p className="company-empty">Start and run a financial review before generating a report.</p>}
        {detail && !detail.runs.length && <p className="company-empty">Run financial checks for this review before generating a report.</p>}
        {detail?.runs.length > 0 && <div className="company-report"><h2>{overview.company.business_name} — Financial review</h2><p>Period: {detail.engagement.period_start} to {detail.engagement.period_end} · Latest check: {date(detail.runs[0].created_at)}</p><p>{detail.invoices.length} invoice records · {detail.findings.length} recorded issues</p><p>Displayed results: {detail.latest_results.filter((item) => item.outcome === 'PASS').length} passed · {detail.latest_results.filter((item) => item.outcome === 'EXCEPTION').length} flagged · {detail.latest_results.filter((item) => item.outcome === 'INSUFFICIENT EVIDENCE').length} need more information</p><h3>Recorded issues</h3>{detail.findings.length ? detail.findings.map((item) => <article className="company-issue" key={item.finding_id}><strong>{item.title} — {item.status}</strong><p>{item.description}</p><small>Source: {documents.documents.find((file) => file.file_id === item.evidence_refs?.[0]?.file_id)?.file_name || 'See linked invoice record'}</small></article>) : <p>No issues were recorded. This does not mean every check could be completed.</p>}<p className="company-note">This report shows up to 5,000 results and 500 issues. Checks without enough information are not passes.</p></div>}
      </section>}

      {page === 'reconciliation' && <>
        <div className="reconciliation-hero">
          <div className="reconciliation-pillars">
            <div className="pillar-card">
              <span className="pillar-icon">📦</span>
              <strong>1. Purchase Orders</strong>
              <small>Approved internal commitments, quantities, and agreed contracted unit rates.</small>
            </div>
            <div className="pillar-divider">⟷</div>
            <div className="pillar-card">
              <span className="pillar-icon">📄</span>
              <strong>2. Vendor Invoices</strong>
              <small>Billed liabilities, supplier GSTINs, tax breakdowns, and payment terms.</small>
            </div>
            <div className="pillar-divider">⟷</div>
            <div className="pillar-card">
              <span className="pillar-icon">🏦</span>
              <strong>3. Bank Settlements</strong>
              <small>Actual cash disbursements, clearing dates, and electronic UTR references.</small>
            </div>
          </div>
        </div>

        <section className="company-panel">
          <div className="company-section-heading">
            <div>
              <h2>Reconciliation & Anomaly Patrol</h2>
              <p>Cross-examine your purchase orders, invoices, and bank statements to detect duplicate billing, unbacked debits, and price variances.</p>
            </div>
            <Button onClick={matchRecords} disabled={working}>
              {working ? 'Running patrol…' : 'Run Reconciliation Patrol'}
            </Button>
          </div>

          {patrol && (
            <>
              <div className="company-metrics" style={{ marginTop: '16px' }}>
                <section className="company-metric">
                  <span>Invoices Scanned</span>
                  <strong>{patrol.total_invoices_scanned}</strong>
                  <small>Extracted AP Liabilities</small>
                </section>
                <section className="company-metric">
                  <span>Bank Records Checked</span>
                  <strong>{patrol.total_bank_transactions}</strong>
                  <small>Cash & POS Settlements</small>
                </section>
                <section className="company-metric">
                  <span>Reconciled Records</span>
                  <strong style={{ color: '#16a34a' }}>{patrol.matched_count}</strong>
                  <small>Exact & Verified Matches</small>
                </section>
                <section className="company-metric">
                  <span>Flagged Discrepancies</span>
                  <strong style={{ color: patrol.anomalies_detected > 0 ? '#dc2626' : 'inherit' }}>
                    {patrol.anomalies_detected}
                  </strong>
                  <small>Requiring AI/Auditor Review</small>
                </section>
              </div>

              {patrol.anomalies.length > 0 && (
                <div style={{ marginTop: '24px' }}>
                  <h3 style={{ fontSize: '15px', fontWeight: 600, marginBottom: '12px' }}>
                    Flagged Discrepancies & AI Cascade Root-Cause Analysis
                  </h3>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    {patrol.anomalies.map((item) => (
                      <article className="company-issue" key={item.anomaly_id} style={{ borderLeft: item.severity === 'high' ? '4px solid #dc2626' : '4px solid #f59e0b' }}>
                        <div className="company-issue-heading" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <strong style={{ fontSize: '14px' }}>{item.title}</strong>
                          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                            <span className={`severity-pill ${item.severity}`}>{item.severity.toUpperCase()}</span>
                            <span className="cascade-tier-pill">{item.cascade_tier}</span>
                          </div>
                        </div>
                        <p style={{ margin: '8px 0 6px', color: 'var(--text)' }}>{item.description}</p>
                        
                        {item.ai_root_cause && (
                          <div className="ai-root-cause-box">
                            <strong>🧠 AI Cascade Root-Cause Diagnosis:</strong>
                            <p>{item.ai_root_cause}</p>
                          </div>
                        )}

                        {item.proposal_action && (
                          <div className="ai-proposal-box">
                            <strong>⚖️ Auditor Compliance Action:</strong>
                            <p>{item.proposal_action}</p>
                          </div>
                        )}

                        <div style={{ display: 'flex', gap: '10px', marginTop: '12px' }}>
                          <button className="company-link" onClick={() => downloadLetter(item.affected_ids?.[0])}>
                            📄 Download Formal Dispute Letter (PDF)
                          </button>
                          <span>·</span>
                          <button className="company-link" onClick={() => { onAskDocument({ file_name: item.affected_ids?.[0] || item.title }); navigate('search') }}>
                            💬 Ask AI Assistant about this discrepancy
                          </button>
                        </div>
                      </article>
                    ))}
                  </div>
                </div>
              )}

              {patrol.matched_pairs && patrol.matched_pairs.length > 0 && (
                <div style={{ marginTop: '28px' }}>
                  <h3 style={{ fontSize: '15px', fontWeight: 600, marginBottom: '12px' }}>
                    Reconciled Records ({patrol.matched_pairs.length} Matched)
                  </h3>
                  <div className="company-table-wrap">
                    <table className="company-table">
                      <thead>
                        <tr>
                          <th>Invoice #</th>
                          <th>Vendor / Beneficiary</th>
                          <th>Invoice Date</th>
                          <th>Invoiced</th>
                          <th>Bank Debit</th>
                          <th>Bank Date</th>
                          <th>Reference / UTR</th>
                          <th>Status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {patrol.matched_pairs.map((pair, pIdx) => (
                          <tr key={pair.match_id || pIdx}>
                            <td><strong>{pair.invoice_number}</strong></td>
                            <td>{pair.vendor}</td>
                            <td>{pair.invoice_date}</td>
                            <td>INR {pair.invoice_total.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</td>
                            <td>INR {pair.bank_debit.toLocaleString('en-IN', { minimumFractionDigits: 2 })}</td>
                            <td>{pair.bank_date}</td>
                            <td><code style={{ fontSize: '11px', background: 'var(--surface-raised)', padding: '2px 4px', borderRadius: '4px' }}>{pair.reference}</code></td>
                            <td>
                              <span style={{ color: pair.variance === 0 ? '#16a34a' : '#d97706', fontWeight: 600, fontSize: '12px' }}>
                                {pair.match_status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </>
          )}

          {!patrol && (
            <p className="company-empty" style={{ margin: '20px 0' }}>
              Click <strong>Run Reconciliation Patrol</strong> to audit and match purchase orders, invoices, and bank statements with the AI Cascade.
            </p>
          )}
        </section>
      </>}

      {page === 'settings' && <section className="company-panel"><h2>Company settings</h2><dl className="company-details"><div><dt>Company</dt><dd>{overview.company.business_name || 'Not available'}</dd></div><div><dt>Account</dt><dd>{profile.username || profile.email}</dd></div><div><dt>Company status</dt><dd>{overview.company.status || 'Not available'}</dd></div></dl><h3>Connected services</h3><p>Manage sources that can bring financial records into your workspace.</p><div className="company-actions"><Button secondary onClick={openEmail}>Manage email connection</Button><Button secondary onClick={openPos}>Manage point of sale</Button></div></section>}
    </>}
  </div>
}
