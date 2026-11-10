import { useEffect, useRef, useState } from 'react'
import AdminUsers from './AdminUsers.jsx'
import AuditWorkspace from './AuditWorkspace.jsx'
import { AuthLoadingScreen, ProfileAccessScreen, SignInScreen } from './auth/AuthScreens.jsx'
import { useAuth } from './auth/AuthContext.jsx'

const recentChats = [
  { title: 'Vendor payment review', prompt: 'Was invoice INV-1048 paid twice?' },
  { title: 'Unmatched receipt', prompt: 'Find expenses missing receipts' },
  { title: 'September close', prompt: 'Summarise the audit evidence' },
]

const suggestions = [
  { title: 'Check for duplicate payments', description: 'Compare invoices with bank activity' },
  { title: 'Find expenses missing receipts', description: 'Review expenses against receipts' },
  { title: 'Summarise the evidence', description: 'Get a brief overview with source links' },
  { title: 'Show items to review', description: 'See findings that need your attention' },
]

function Icon({ name, size = 17 }) {
  const paths = {
    plus: <path d="M12 5v14M5 12h14" />,
    menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></>,
    attach: <><path d="m21.4 11.1-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8l9.2-9.2a3.7 3.7 0 0 1 5.2 5.2l-9.2 9.2a1.9 1.9 0 0 1-2.6-2.6l8.5-8.5" /></>,
    sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" /></>,
    moon: <path d="M20.9 13A8.5 8.5 0 0 1 11 3.1 8.6 8.6 0 1 0 20.9 13Z" />,
    file: <><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13h8M8 17h8" /></>,
    chevron: <path d="m9 18 6-6-6-6" />,
    close: <><path d="m18 6-12 12M6 6l12 12" /></>,
    mail: <><rect width="20" height="16" x="2" y="4" rx="2" /><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" /></>,
    refresh: <><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M3 21v-5h5" /></>,
    lock: <><rect width="18" height="11" x="3" y="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></>,
    check: <path d="M20 6 9 17l-5-5" />,
    cart: <><circle cx="8" cy="21" r="1" /><circle cx="19" cy="21" r="1" /><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12" /></>,
    barcode: <><path d="M3 5v14M8 5v14M12 5v14M17 5v14M21 5v14M6 5v14M15 5v14" /></>,
    store: <><path d="m2 7 4.41-4.41A2 2 0 0 1 7.83 2h8.34a2 2 0 0 1 1.42.59L22 7" /><path d="M4 12v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8" /><path d="M15 22v-4a2 2 0 0 0-2-2h-2a2 2 0 0 0-2 2v4" /><path d="M2 7h20" /></>,
  }

  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  )
}

function formatActivityStep(message) {
  if (message.includes('question is received')) return 'Question received'
  if (message.includes('Parsing and saving your files')) return 'Preparing uploaded files'
  const fileMatch = message.match(/Parsing, saving and indexing (.+?)(?:…|\.\.\.)?$/)
  if (fileMatch) return `Processing ${fileMatch[1]}`
  if (message.includes('Finding document chunks')) return 'Searching saved documents'
  return 'Preparing document search'
}

function getSampleAnswer(prompt) {
  const normalized = prompt.toLowerCase()

  if (normalized.includes('receipt')) {
    return {
      text: 'I found one expense in the sample register that does not appear to have a matching receipt. It may have been filed separately, so this needs a quick check before it is treated as a missing document.',
      finding: 'Receipt not located',
      detail: 'Expense register row 32 lists $86.40 for office supplies. No matching receipt was found in the sample receipt bundle.',
      sources: ['Expense register · row 32', 'Receipt bundle · 9 pages'],
    }
  }

  if (normalized.includes('summar')) {
    return {
      text: 'The sample files cover September transactions, several supplier invoices, and expense receipts. I found two items that may need a closer look; both are linked below.',
      finding: '2 items for review',
      detail: 'One payment amount differs from the invoice, and one expense has no matching receipt in the files provided.',
      sources: ['Bank statement · Sep', 'Expense register · rows 18, 27, 32'],
    }
  }

  if (normalized.includes('attention') || normalized.includes('review') || normalized.includes('item')) {
    return {
      text: 'Two items in the sample records may need your attention. I’ve linked the source rows so you can review them in context.',
      finding: '2 items for review',
      detail: 'A supplier payment does not match its invoice amount, and a small office expense has no matching receipt in the files provided.',
      sources: ['Invoice INV-1048 · page 1', 'Expense register · row 32'],
    }
  }

  return {
    text: 'I found two bank entries that may relate to invoice INV-1048. The amounts differ, so the records do not confirm a duplicate payment on their own.',
    finding: 'Possible amount mismatch',
    detail: 'The invoice is for $1,240.00. The similar statement entries are $1,240.00 and $1,060.00; the second entry may be unrelated.',
    sources: ['Invoice INV-1048 · page 1', 'Bank statement · rows 18, 27'],
  }
}

function App() {
  const approveEmailDraft = async (proposal) => {
    try {
      const res = await fetch("/api/actions/approve-email", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(proposal.payload)
      });
      const data = await res.json();
      showNotice(data.message || "Dispute email approved and queued!");
    } catch (err) {
      showNotice("Email delivery approval failed: " + err.message);
    }
  };
  const { loading: authLoading, session, profile, signOut, apiFetch } = useAuth()
  const [activeView, setActiveView] = useState('dashboard')
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('ledgerline-react-theme-v2') || 'light'
    } catch {
      return 'light'
    }
  })
  const [messages, setMessages] = useState([])
  const [draft, setDraft] = useState('')
  const [attachedFiles, setAttachedFiles] = useState([])
  const [knownFiles, setKnownFiles] = useState([])
  const [isBusy, setIsBusy] = useState(false)
  const [activeChat, setActiveChat] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [notice, setNotice] = useState('')
  const fileInput = useRef(null)
  const noticeTimer = useRef(null)
  const conversationEnd = useRef(null)

  useEffect(() => {
    document.documentElement.style.colorScheme = theme
    document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#17181b' : '#f1f2f5'
    try {
      localStorage.setItem('ledgerline-react-theme-v2', theme)
    } catch {
      // The theme still works if storage is unavailable.
    }
  }, [theme])

  useEffect(() => {
    conversationEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  useEffect(() => () => window.clearTimeout(noticeTimer.current), [])

  useEffect(() => {
    const handlePageChange = (event) => setActiveView(event.detail)
    window.addEventListener('audit-page', handlePageChange)
    return () => window.removeEventListener('audit-page', handlePageChange)
  }, [])

  function showNotice(message) {
    setNotice(message)
    window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(''), 2400)
  }

  function startNewChat() {
    if (isBusy) return
    setActiveView('search')
    setMessages([])
    setDraft('')
    setAttachedFiles([])
    setActiveChat('')
    setSidebarOpen(false)
  }

  // Email Ingestion Connector State & Handlers
  const [connectorStatus, setConnectorStatus] = useState({
    is_authorized: false,
    mode: 'demo',
    provider: 'demo',
    email_address: null,
    synced_count: 0,
    recent_items: [],
  })
  const [connectorModalOpen, setConnectorModalOpen] = useState(false)
  const [connectorMode, setConnectorMode] = useState('demo')
  const [connectorProvider, setConnectorProvider] = useState('gmail')
  const [connectorEmail, setConnectorEmail] = useState('')
  const [connectorPassword, setConnectorPassword] = useState('')
  const [connectorHost, setConnectorHost] = useState('imap.gmail.com')
  const [connectorPort, setConnectorPort] = useState(993)
  const [isAuthorizing, setIsAuthorizing] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)

  const fetchConnectorStatus = async () => {
    try {
      const res = await fetch('/api/connectors/email/status')
      if (res.ok) {
        const data = await res.json()
        setConnectorStatus(data)
      }
    } catch (err) {
      console.error('Failed to load connector status', err)
    }
  }

  useEffect(() => {
    fetchConnectorStatus()
  }, [])

  const handleProviderSelect = (prov) => {
    setConnectorProvider(prov)
    if (prov === 'gmail') {
      setConnectorHost('imap.gmail.com')
      setConnectorPort(993)
    } else if (prov === 'outlook') {
      setConnectorHost('outlook.office365.com')
      setConnectorPort(993)
    } else if (prov === 'yahoo') {
      setConnectorHost('imap.mail.yahoo.com')
      setConnectorPort(993)
    } else {
      setConnectorHost('')
      setConnectorPort(993)
    }
  }

  const handleAuthorize = async (e) => {
    if (e) e.preventDefault()
    setIsAuthorizing(true)
    try {
      const payload = {
        mode: connectorMode,
        provider: connectorMode === 'demo' ? 'demo' : connectorProvider,
        email_address: connectorMode === 'demo' ? (connectorEmail || 'accounts@smallbiz.local') : connectorEmail,
        password: connectorMode === 'demo' ? null : connectorPassword,
        imap_host: connectorMode === 'demo' ? 'demo-mailbox.internal' : connectorHost,
        imap_port: connectorPort || 993,
      }
      const res = await fetch('/api/connectors/email/authorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Authorization failed')
      setConnectorStatus(data.status)
      showNotice(data.message || 'Email connector authorized successfully!')
    } catch (err) {
      showNotice('Authorization Error: ' + err.message)
    } finally {
      setIsAuthorizing(false)
    }
  }

  const handleRevoke = async () => {
    try {
      const res = await fetch('/api/connectors/email/revoke', { method: 'POST' })
      const data = await res.json()
      setConnectorStatus(data.status)
      showNotice('Mailbox access revoked.')
    } catch (err) {
      showNotice('Failed to revoke access: ' + err.message)
    }
  }

  const handleSync = async () => {
    if (isSyncing) return
    setIsSyncing(true)
    try {
      const res = await fetch('/api/connectors/email/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_items: 2 }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Failed to sync mailbox')

      await fetchConnectorStatus()
      showNotice(`Successfully synced ${data.invoices_ingested} invoice(s) from mailbox!`)

      // Add newly synced attachments to known files in sidebar
      const newFiles = []
      ;(data.items || []).forEach((item) => {
        (item.attachments || []).forEach((att) => {
          if (!knownFiles.some((f) => f.name === att) && !newFiles.some((f) => f.name === att)) {
            newFiles.push({ name: att })
          }
        })
      })
      if (newFiles.length) {
        setKnownFiles((prev) => [...prev, ...newFiles])
      }

      // Add an assistant chat message detailing the automated ingestion and audit
      const assistantId = `assistant-sync-${Date.now()}`
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: 'assistant',
          busy: false,
          status: '',
          activityExpanded: false,
          activity: [
            { label: `Connected to mailbox (${data.email_account})`, state: 'done' },
            { label: `Extracted ${data.invoices_ingested} invoice attachment(s)`, state: 'done' },
            { label: 'Parsed GST provenance & indexed vector chunks in Supabase', state: 'done' },
          ],
          chunks: [],
          warnings: [],
          files: (data.items || []).flatMap((it) => (it.attachments || []).map((att) => ({ file_name: att }))),
          resultsReceived: true,
          llmCalled: true,
          answer: `Automated Ingestion: Successfully scanned ${data.emails_scanned} email(s) and ingested ${data.invoices_ingested} invoice document(s) from ${data.email_account || 'mailbox'}. Evidence has been indexed into your Supabase RAG workspace.`,
          proposals: data.proposals || [],
        },
      ])
    } catch (err) {
      showNotice('Sync error: ' + err.message)
    } finally {
      setIsSyncing(false)
    }
  }

  // Point-of-Sale (POS) & Billing Connector State & Handlers
  const [posStatus, setPosStatus] = useState({
    is_authorized: false,
    mode: 'demo',
    provider: 'demo',
    store_name: 'SmallBiz Supermart',
    gstin: '07AAAAA0000A1Z5',
    terminal_id: 'POS-TERMINAL-01',
    has_api_key: false,
    webhook_endpoint: '/api/connectors/pos/webhook',
    bills_synced_count: 0,
    total_sales_amount: 0.0,
    recent_bills: [],
    catalog_items: [],
  })
  const [posModalOpen, setPosModalOpen] = useState(false)
  const [posMode, setPosMode] = useState('demo')
  const [posProvider, setPosProvider] = useState('vyapar')
  const [posStoreName, setPosStoreName] = useState('Gupta Kirana & General Store')
  const [posGstin, setPosGstin] = useState('07AAACG1234F1Z8')
  const [posTerminalId, setPosTerminalId] = useState('COUNTER-01')
  const [posSessionApiKey, setPosSessionApiKey] = useState('')
  const [isAuthorizingPOS, setIsAuthorizingPOS] = useState(false)
  const [isScanningBarcode, setIsScanningBarcode] = useState(false)
  const [isSyncingPOSBatch, setIsSyncingPOSBatch] = useState(false)
  const [selectedBarcode, setSelectedBarcode] = useState('8901030383712')
  const [scanQty, setScanQty] = useState(1)
  const [scanPaymentMethod, setScanPaymentMethod] = useState('UPI')

  const fetchPOSStatus = async () => {
    try {
      const res = await fetch('/api/connectors/pos/status')
      if (res.ok) {
        const data = await res.json()
        setPosStatus(data)
      }
    } catch (err) {
      console.error('Failed to load POS status', err)
    }
  }

  useEffect(() => {
    fetchPOSStatus()
  }, [])

  const handleAuthorizePOS = async (e) => {
    if (e) e.preventDefault()
    setIsAuthorizingPOS(true)
    try {
      const payload = {
        mode: posMode,
        provider: posMode === 'demo' ? 'demo' : posProvider,
        store_name: posStoreName,
        gstin: posGstin,
        terminal_id: posTerminalId,
      }
      const res = await fetch('/api/connectors/pos/authorize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'POS Authorization failed')
      setPosStatus(data.status)
      if (data.api_key) setPosSessionApiKey(data.api_key)
      showNotice(data.message || 'POS Connector authorized!')
    } catch (err) {
      showNotice('POS Auth Error: ' + err.message)
    } finally {
      setIsAuthorizingPOS(false)
    }
  }

  const handleRevokePOS = async () => {
    try {
      const res = await fetch('/api/connectors/pos/revoke', { method: 'POST' })
      const data = await res.json()
      setPosStatus(data.status)
      setPosSessionApiKey('')
      showNotice('POS connection revoked.')
    } catch (err) {
      showNotice('Failed to revoke POS: ' + err.message)
    }
  }

  const handleScanBarcode = async () => {
    if (isScanningBarcode) return
    setIsScanningBarcode(true)
    try {
      const res = await fetch('/api/connectors/pos/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          barcode: selectedBarcode,
          quantity: scanQty,
          payment_method: scanPaymentMethod,
        }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Barcode scan failed')

      await fetchPOSStatus()
      showNotice(`Billed ${data.bill_number}: INR ${data.grand_total}`)

      // Add generated bill to known files in sidebar
      if (data.file_name && !knownFiles.some((f) => f.name === data.file_name)) {
        setKnownFiles((prev) => [...prev, { name: data.file_name }])
      }

      // Add assistant message with POS bill and action cards
      const assistantId = `assistant-pos-${Date.now()}`
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: 'assistant',
          busy: false,
          status: '',
          activityExpanded: false,
          activity: [
            { label: `Scanned Barcode: ${selectedBarcode}`, state: 'done' },
            { label: `Generated GST Cash Memo: ${data.bill_number}`, state: 'done' },
            { label: 'Indexed in Supabase & verified tax compliance', state: 'done' },
          ],
          chunks: [],
          warnings: (data.tax_anomalies || []).map((w) => ({ file_name: data.file_name, message: w })),
          files: [{ file_name: data.file_name }],
          resultsReceived: true,
          llmCalled: true,
          answer: `POS Checkout Registered: Successfully generated retail invoice ${data.bill_number} for INR ${data.grand_total} (Paid via ${scanPaymentMethod}). Indexed into your Supabase RAG workspace.`,
          proposals: data.proposals || [],
        },
      ])
    } catch (err) {
      showNotice('Scan Error: ' + err.message)
    } finally {
      setIsScanningBarcode(false)
    }
  }

  const handleSyncPOSBatch = async () => {
    if (isSyncingPOSBatch) return
    setIsSyncingPOSBatch(true)
    try {
      const res = await fetch('/api/connectors/pos/sync-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_size: 3 }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'POS Batch sync failed')

      await fetchPOSStatus()
      showNotice(`Synced ${data.bills_ingested} bills from POS register!`)

      const newFiles = (data.bills || [])
        .map((b) => ({ name: b.file_name }))
        .filter((b) => !knownFiles.some((kf) => kf.name === b.name))
      if (newFiles.length) {
        setKnownFiles((prev) => [...prev, ...newFiles])
      }

      const assistantId = `assistant-pos-batch-${Date.now()}`
      setMessages((current) => [
        ...current,
        {
          id: assistantId,
          role: 'assistant',
          busy: false,
          status: '',
          activityExpanded: false,
          activity: [
            { label: `Synced ${data.bills_ingested} bills from daily POS register`, state: 'done' },
            { label: `Reconciliation Patrol: ${data.reconciliation_digest?.summary_message}`, state: 'done' },
          ],
          chunks: [],
          warnings: [],
          files: (data.bills || []).map((b) => ({ file_name: b.file_name })),
          resultsReceived: true,
          llmCalled: true,
          answer: `POS Sales Batch Ingestion: Synced ${data.bills_ingested} register sales bills. Reconciliation Patrol evaluated sales against bank statement settlements.`,
          proposals: data.proposals || [],
        },
      ])
    } catch (err) {
      showNotice('POS Sync Error: ' + err.message)
    } finally {
      setIsSyncingPOSBatch(false)
    }
  }

  async function sendMessage(value = draft) {
    const question = value.trim()
    if (!question || isBusy) return

    const files = [...attachedFiles]
    const fileNames = files.map((file) => file.name)
    const assistantId = `assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`

    setMessages((current) => [
      ...current,
      { id: `user-${assistantId}`, role: 'user', text: question, files: fileNames },
      { id: assistantId, role: 'assistant', busy: true, status: '', activityExpanded: true, activity: [], chunks: [], warnings: [], files: [], resultsReceived: false },
    ])
    setDraft('')
    setAttachedFiles([])
    setActiveChat(question.length > 33 ? `${question.slice(0, 30)}…` : question)
    setIsBusy(true)

    const updateAssistant = (updates) => {
      setMessages((current) => current.map((message) => (
        message.id === assistantId
          ? { ...message, ...(typeof updates === 'function' ? updates(message) : updates) }
          : message
      )))
    }

    try {
      const body = new FormData()
      body.append('question', question)
      files.forEach((file) => body.append('files', file, file.name))

      if (files.length && !['administrator', 'auditor'].includes(profile.role)) {
        throw new Error('Only auditors and administrators can upload evidence.')
      }
      const response = await apiFetch('/api/query', { method: 'POST', body })
      if (!response.ok) throw new Error(`The server returned ${response.status}.`)
      if (!response.body) throw new Error('This browser could not read the streamed response.')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      const handleEvent = (block) => {
        const dataLine = block.split(/\r?\n/).find((line) => line.startsWith('data:'))
        if (!dataLine) return
        const event = JSON.parse(dataLine.slice(5).trim())

        if (event.type === 'status') {
          if (event.done) {
            updateAssistant((message) => ({
              busy: false,
              searchComplete: true,
              activityExpanded: false,
              activity: message.activity.map((step) => ({ ...step, state: 'done' })),
            }))
            setIsBusy(false)
          } else {
            const label = formatActivityStep(event.message)
            updateAssistant((message) => {
              const lastStep = message.activity[message.activity.length - 1]
              if (lastStep?.label === label && lastStep.state === 'active') {
                return { currentStep: label }
              }
              return {
                currentStep: label,
                activity: [
                  ...message.activity.map((step) => ({ ...step, state: 'done' })),
                  { label, state: 'active' },
                ],
              }
            })
          }
        } else if (event.type === 'file_indexed') {
          setKnownFiles((current) => current.some((file) => file.name === event.file_name)
            ? current
            : [...current, { name: event.file_name }])
          updateAssistant((message) => ({
            activity: [
              ...message.activity.map((step) => ({ ...step, state: 'done' })),
              { label: `Added ${event.file_name} to search`, state: 'done' },
            ],
          }))
        } else if (event.type === 'results') {
          updateAssistant({
            chunks: event.chunks || [],
            warnings: event.warnings || [],
            files: event.files || [],
            llmCalled: event.llm_called,
            answer: event.llm_called && event.answer ? event.answer : '',
            stoppedTier: event.stopped_tier,
            stoppedModel: event.stopped_model,
            tierHistory: event.tier_history || [],
            proposals: event.proposals || [],
            resultsReceived: true,
          })
          ;(event.files || []).forEach((file) => {
            setKnownFiles((current) => current.some((known) => known.name === file.file_name)
              ? current
              : [...current, { name: file.file_name }])
          })
        } else if (event.type === 'error') {
          updateAssistant((message) => ({
            status: event.message,
            error: true,
            busy: false,
            searchComplete: false,
            activityExpanded: true,
            activity: message.activity.map((step, index) => ({
              ...step,
              state: index === message.activity.length - 1 && step.state === 'active' ? 'failed' : 'done',
            })),
            warnings: event.warnings || [],
          }))
          setIsBusy(false)
        }
      }

      while (true) {
        const { value: bytes, done } = await reader.read()
        buffer += decoder.decode(bytes || new Uint8Array(), { stream: !done })
        const blocks = buffer.split(/\r?\n\r?\n/)
        buffer = blocks.pop() || ''
        blocks.forEach(handleEvent)
        if (done) break
      }
      if (buffer.trim()) handleEvent(buffer)
      updateAssistant((message) => ({
        busy: false,
        searchComplete: message.searchComplete || !message.error,
        activityExpanded: message.error ? true : false,
        activity: message.activity.map((step) => ({ ...step, state: step.state === 'failed' ? 'failed' : 'done' })),
      }))
      setIsBusy(false)
    } catch (error) {
      updateAssistant((message) => ({
        status: `Could not connect to the retrieval service: ${error.message}`,
        error: true,
        busy: false,
        searchComplete: false,
        activityExpanded: true,
        activity: message.activity.map((step, index) => ({
          ...step,
          state: index === message.activity.length - 1 && step.state === 'active' ? 'failed' : 'done',
        })),
      }))
      setIsBusy(false)
    }
  }

  function selectRecentChat(chat) {
    setActiveView('search')
    setSidebarOpen(false)
    sendMessage(chat.prompt)
  }

  function handleFiles(event) {
    if (!['administrator', 'auditor'].includes(profile.role)) {
      showNotice('Your role can search records but cannot upload evidence.')
      event.target.value = ''
      return
    }
    const files = Array.from(event.target.files || [])
    if (!files.length) return
    setAttachedFiles((current) => [...current, ...files])
    showNotice(`${files.length} file${files.length === 1 ? '' : 's'} added to this draft`)
    event.target.value = ''
  }

  if (authLoading) return <AuthLoadingScreen />
  if (!session) return <SignInScreen />
  if (!profile) return <ProfileAccessScreen />

  return (
    <div className={`app-shell theme-${theme}`}>
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-row">
          <div className="brand"><span className="brand-mark">L</span><span>ledgerline</span></div>
          <button className="icon-button sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="Close menu"><Icon name="close" /></button>
          <button className="icon-button desktop-add" onClick={startNewChat} aria-label="Start a new conversation"><Icon name="plus" /></button>
        </div>

        <button className="new-chat-button" onClick={startNewChat} disabled={isBusy}><Icon name="plus" size={16} /> New conversation</button>

        <div className="sidebar-section workspace-navigation">
          <div className="section-heading">AUDIT WORKSPACE</div>
          <nav className="workspace-nav" aria-label="Audit workspace">
            {[['dashboard', 'Audit dashboard'], ['engagements', 'Engagements'], ['findings', 'Audit findings']].map(([view, label]) => (
              <button key={view} className={`workspace-nav-item ${activeView === view ? 'is-selected' : ''}`} onClick={() => { setActiveView(view); setSidebarOpen(false) }}>
                <span className="nav-indicator" />{label}
              </button>
            ))}
            <button className={`workspace-nav-item ${activeView === 'search' ? 'is-selected' : ''}`} onClick={() => { setActiveView('search'); setSidebarOpen(false) }}>
              <span className="nav-indicator" />Document search
            </button>
            {profile.role === 'administrator' && <button className={`workspace-nav-item ${activeView === 'users' ? 'is-selected' : ''}`} onClick={() => { setActiveView('users'); setSidebarOpen(false) }}>
              <span className="nav-indicator" />User access
            </button>}
          </nav>
        </div>

        <div className="sidebar-section">
          <div className="section-heading">RECENT CHATS</div>
          <nav className="recent-list" aria-label="Recent chats">
            {recentChats.map((chat) => (
              <button className={`recent-chat ${activeChat === chat.title ? 'is-selected' : ''}`} key={chat.title} onClick={() => selectRecentChat(chat)}>
                <span className="recent-indicator" />
                <span className="recent-title">{chat.title}</span>
              </button>
            ))}
          </nav>
        </div>

        <div className="sidebar-section files-section">
          <div className="section-heading file-heading"><span>UPLOADED FILES</span><span className="file-total">{knownFiles.length} files</span></div>
          <div className="file-list">
            {knownFiles.length ? knownFiles.map((file) => (
              <div className="evidence-file" key={file.name} title={file.name}>
                <span className="file-type">{file.name.split('.').pop()?.slice(0, 4).toUpperCase()}</span><span className="file-title">{file.name}</span>
              </div>
            )) : <div className="file-empty">Files attached to a query will appear here.</div>}
          </div>
        </div>

        <div className="sidebar-footer">
          <div className="workspace-status"><span className="status-dot" /> Supabase document search</div>
          <div className="profile-row"><span className="profile-avatar">{(profile.email || 'U').slice(0, 1).toUpperCase()}</span><span className="profile-copy"><strong>{profile.email || 'Signed-in user'}</strong><small>{profile.role.replaceAll('_', ' ')}</small></span><button className="profile-more" onClick={() => signOut()} aria-label="Sign out">↪</button></div>
        </div>
      </aside>

      {sidebarOpen && <button className="mobile-backdrop" onClick={() => setSidebarOpen(false)} aria-label="Close navigation" />}

      <main className="main-panel">
        {activeView === 'search' ? <>
        <header className="topbar">
          <div className="case-heading">
            <button className="icon-button mobile-menu-button" onClick={() => setSidebarOpen(true)} aria-label="Open menu"><Icon name="menu" /></button>
            <div><div className="case-title">Financial workspace <span>/</span> Document search</div><div className="case-subtitle">RAG retrieval · 4-tier LLM cascade decision maker</div></div>
          </div>
          <div className="topbar-actions">
            <button
              className={`connector-badge ${connectorStatus.is_authorized ? 'is-connected' : ''}`}
              onClick={() => setConnectorModalOpen(true)}
              title="Configure Small Business Email Ingestion Connector"
              aria-label="Manage Email Connector"
            >
              <span className={`connector-dot ${connectorStatus.is_authorized ? 'is-active' : ''}`} />
              <Icon name="mail" size={14} />
              <span>
                {connectorStatus.is_authorized
                  ? `${connectorStatus.email_address || 'Connected'} (${connectorStatus.mode === 'demo' ? 'Demo' : 'Live'})`
                  : 'Connect Email Inbox'}
              </span>
            </button>

            <button
              className={`connector-badge ${posStatus.is_authorized ? 'is-connected' : ''}`}
              onClick={() => setPosModalOpen(true)}
              title="Configure Point-of-Sale (POS) & Billing Software Connector"
              aria-label="Manage POS Connector"
            >
              <span className={`connector-dot ${posStatus.is_authorized ? 'is-active' : ''}`} />
              <Icon name="cart" size={14} />
              <span>
                {posStatus.is_authorized
                  ? `${posStatus.store_name} (${posStatus.mode === 'demo' ? 'Demo POS' : 'Live POS'})`
                  : 'Connect POS / Billing'}
              </span>
            </button>

            <span className="sample-label">LIVE RETRIEVAL</span>
            <span className="sample-label">{profile.role.replaceAll('_', ' ').toUpperCase()}</span>
            <button className="theme-button" onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}><Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} /><span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span></button>
            <button className="case-details" onClick={() => showNotice('Retrieved chunks and LLM cascade audit decisions are shown below.')}>Search details <span>⌄</span></button>
            <button className="case-details" onClick={signOut}>Sign out</button>
          </div>
        </header>

        <section className="conversation-scroll" aria-label="Audit conversation">
          <div className="conversation-content">
            {messages.length === 0 ? (
              <div className="empty-state">
                <div className="eyebrow">YOUR DOCUMENTS · SEARCH</div>
                <h1>What would you like to review?</h1>
                <p>Ask a question about your saved documents or attach new files. I’ll show the matching text chunks and their source files.</p>
                <div className="suggestion-grid">
                  {suggestions.map((suggestion) => (
                    <button className="suggestion-card" key={suggestion.title} onClick={() => sendMessage(suggestion.title)}>
                      <strong>{suggestion.title}</strong><span>{suggestion.description}</span><Icon name="chevron" size={15} />
                    </button>
                  ))}
                </div>
                <div className="empty-note"><Icon name="file" size={15} /> Retrieved passages will appear with their source file</div>
              </div>
            ) : (
              <div className="message-list">
                {messages.map((message, index) => message.role === 'user' ? (
                  <article className="message-row user-row" key={message.id || `user-${index}`}>
                    <div className="message-content"><div className="message-label">You</div><div className="user-bubble">{message.text}</div>{message.files?.length > 0 && <div className="sent-files">{message.files.map((file) => <span key={file}><Icon name="file" size={12} />{file}</span>)}</div>}</div>
                    <div className="message-avatar user-avatar">A</div>
                  </article>
                ) : (
                  <article className="message-row assistant-row" key={message.id || `assistant-${index}`}>
                    <div className="message-avatar assistant-avatar">L</div>
                    <div className="message-content answer-content">
                      <div className="message-label">Ledgerline <span>·</span> Search result</div>

                      <section className={`activity-card ${message.error ? 'activity-error' : ''}`} aria-live="polite">
                        <div className="activity-header">
                          <span className={`activity-indicator ${message.busy ? 'is-working' : message.error ? 'has-failed' : 'is-done'}`} />
                          <div className="activity-copy"><strong>Search activity</strong><span>{message.busy ? (message.currentStep || 'Starting search…') : message.error ? 'Search stopped' : 'Search complete'}</span></div>
                          {!message.busy && message.activity.length > 0 && <button className="activity-toggle" onClick={() => updateAssistant({ activityExpanded: !message.activityExpanded })}>{message.activityExpanded ? 'Hide steps' : 'View steps'}</button>}
                        </div>
                        {(message.busy || message.activityExpanded) && message.activity.length > 0 && <ol className="activity-steps">
                          {message.activity.map((step, stepIndex) => <li className={`activity-step step-${step.state}`} key={`${step.label}-${stepIndex}`}><span className="step-marker" /><span>{step.label}</span></li>)}
                        </ol>}
                      </section>

                      {message.error && <section className="request-error" role="alert"><strong>We couldn’t finish the search</strong><span>{message.status}</span></section>}

                      {message.warnings?.length > 0 && <section className="response-section warning-section">
                        <div className="section-title"><h3>File processing notes</h3><span>{message.warnings.length}</span></div>
                        <div className="warning-list">{message.warnings.map((warning) => <div className="file-warning" key={`${warning.file_name}-${warning.message}`}><strong>{warning.file_name}</strong><span>{warning.message}</span></div>)}</div>
                      </section>}

                      {message.files?.length > 0 && <section className="response-section saved-section">
                        <div className="section-title"><h3>Files added to search</h3><span>{message.files.length}</span></div>
                        <div className="saved-file-list">{message.files.map((file) => <div className="saved-file" key={file.file_id || file.file_name}><Icon name="file" size={15} /><span>{file.file_name}</span></div>)}</div>
                      </section>}

                      {message.resultsReceived && <section className="response-section evidence-section">
                        <div className="section-title"><h3>Retrieved evidence</h3><span>{message.chunks.length} {message.chunks.length === 1 ? 'passage' : 'passages'}</span></div>
                        {message.chunks.length > 0 ? <div className="retrieved-chunks">{message.chunks.map((chunk) => <article className="retrieved-chunk" key={chunk.chunk_id}>
                          <div className="chunk-heading"><span className="chunk-file"><Icon name="file" size={15} />{chunk.file_name}</span><span className="chunk-score">Match {Number(chunk.similarity || 0).toFixed(2)}</span></div>
                          <p>{chunk.content}</p>
                          <div className="chunk-source">Source file · {chunk.source_file_id} · chunk {chunk.chunk_index}</div>
                        </article>)}</div> : <div className="no-results"><strong>No matching passages found</strong><span>Try a different question or add another document to this workspace.</span></div>}
                      </section>}

                      {!message.busy && <section className={`answer-panel ${message.error ? 'answer-unavailable' : ''}`}>
                        <div className="section-title"><h3>Answer</h3><span>{message.answer ? 'Generated' : 'Not generated'}</span></div>
                        {message.answer ? <p className="answer-copy">{message.answer}</p> : <div className="answer-empty-state"><span className="answer-empty-mark">i</span><div><strong>{message.error ? 'No answer available' : 'No answer generated'}</strong><p>{message.error ? 'The search did not finish, so there is no result to summarize.' : 'The search finished, but no response could be derived from the provided evidence.'}</p></div></div>}
                      </section>}

                      {!message.busy && message.proposals && message.proposals.length > 0 && (
                        <section className="answer-panel" style={{ marginTop: "12px", borderLeft: "3px solid #3b82f6" }}>
                          <div className="section-title">
                            <h3>Proposed Agentic Actions</h3>
                            <span style={{ color: "#2563eb", fontWeight: 600 }}>{message.proposals.length} Actionable</span>
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "8px" }}>
                            {message.proposals.map((prop, pIdx) => (
                              <div key={prop.action_id || pIdx} style={{ padding: "10px 14px", background: "rgba(59, 130, 246, 0.05)", borderRadius: "6px", border: "1px solid rgba(59, 130, 246, 0.2)" }}>
                                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                                  <strong style={{ fontSize: "13px", color: "#1e3a8a" }}>{prop.title}</strong>
                                  {prop.action_type === "draft_email" && (
                                    <button
                                      type="button"
                                      className="theme-button"
                                      style={{ padding: "4px 10px", fontSize: "11px", background: "#2563eb", color: "#ffffff" }}
                                      onClick={() => approveEmailDraft(prop)}
                                    >
                                      Approve & Queue Email
                                    </button>
                                  )}
                                  {prop.action_type === "generate_pdf" && (
                                    <a
                                      href={prop.payload?.download_url || "/api/actions/generate-letter-pdf"}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="theme-button"
                                      style={{ padding: "4px 10px", fontSize: "11px", textDecoration: "none", background: "#059669", color: "#ffffff" }}
                                    >
                                      Download Notice PDF
                                    </a>
                                  )}
                                </div>
                                <p style={{ fontSize: "12px", margin: "6px 0 0", color: "#475569" }}>{prop.description}</p>
                              </div>
                            ))}
                          </div>
                        </section>
                      )}
                    </div>
                  </article>
                ))}
                <div ref={conversationEnd} />
              </div>
            )}
          </div>
        </section>

        <div className="composer-area">
          {attachedFiles.length > 0 && <div className="attachment-list">{attachedFiles.map((file, index) => <span className="attachment-chip" key={`${file.name}-${index}`}><Icon name="file" size={13} />{file.name}<button type="button" onClick={() => setAttachedFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))} aria-label={`Remove ${file.name}`}>×</button></span>)}</div>}
          <form className="composer" onSubmit={(event) => { event.preventDefault(); sendMessage() }}>
            <textarea value={draft} disabled={isBusy} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage() } }} rows={1} aria-label="Message" placeholder="Ask about an invoice, payment, or expense…" />
            <div className="composer-bottom">{['administrator', 'auditor'].includes(profile.role) && <><button type="button" className="attach-button" disabled={isBusy} onClick={() => fileInput.current?.click()}><Icon name="attach" size={16} /><span>Attach files</span></button><input ref={fileInput} type="file" hidden multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.txt,.xlsx,.xlsm,.wav,.mp3,.ogg,.flac,.aiff,.aif" onChange={handleFiles} /></>}<div className="composer-controls"><span className="enter-hint">Enter to send</span><button className="send-button" type="submit" disabled={!draft.trim() || isBusy}><span>{isBusy ? 'Working…' : 'Send'}</span><Icon name="send" size={14} /></button></div></div>
          </form>
          <div className="composer-note">Files are parsed and saved · Queries are audited dynamically by the LLM cascade decision maker</div>
        </div>
        </> : activeView === 'users' && profile.role === 'administrator' ? <AdminUsers /> : <AuditWorkspace page={activeView} role={profile.role} onOpenSearch={() => setActiveView('search')} />}
      </main>

      {connectorModalOpen && (
        <div className="modal-overlay" onClick={() => setConnectorModalOpen(false)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">
                <Icon name="mail" size={19} />
                <span>Email Ingestion Connector</span>
              </div>
              <button
                className="modal-close"
                onClick={() => setConnectorModalOpen(false)}
                aria-label="Close dialog"
              >
                <Icon name="close" size={16} />
              </button>
            </div>

            <div className="modal-body">
              {connectorStatus.is_authorized ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div className="connected-card">
                    <div className="connected-card-header">
                      <strong style={{ fontSize: '13px' }}>Mailbox Connection Active</strong>
                      <span className="connected-status-pill">
                        <span className="connector-dot is-active" />
                        Authorized
                      </span>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', fontSize: '12px' }}>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>Account</div>
                        <strong style={{ color: 'var(--text)' }}>{connectorStatus.email_address}</strong>
                      </div>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>Mode</div>
                        <strong style={{ color: 'var(--text)' }}>
                          {connectorStatus.mode === 'demo' ? 'Simulated Demo Mailbox' : 'Live IMAP Server'}
                        </strong>
                      </div>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>Server</div>
                        <strong style={{ color: 'var(--text)' }}>{connectorStatus.imap_host || 'Internal'}</strong>
                      </div>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>Invoices Ingested</div>
                        <strong style={{ color: 'var(--text)' }}>{connectorStatus.synced_count} documents</strong>
                      </div>
                    </div>

                    <div style={{ fontSize: '11px', color: 'var(--quiet)' }}>
                      Last Synchronized: {connectorStatus.last_synced_at || 'Just authorized'}
                    </div>
                  </div>

                  <div style={{ display: 'flex', gap: '10px' }}>
                    <button
                      type="button"
                      className="btn-primary"
                      style={{ flex: 1 }}
                      onClick={handleSync}
                      disabled={isSyncing}
                    >
                      <Icon name="refresh" size={15} />
                      <span>{isSyncing ? 'Syncing Invoices…' : 'Sync Mailbox Now'}</span>
                    </button>
                    <button
                      type="button"
                      className="btn-danger"
                      onClick={handleRevoke}
                      disabled={isSyncing}
                    >
                      Revoke Access
                    </button>
                  </div>

                  {connectorStatus.recent_items && connectorStatus.recent_items.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--quiet)', letterSpacing: '0.5px' }}>
                        RECENTLY INGESTED INVOICES
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {connectorStatus.recent_items.map((item, idx) => (
                          <div
                            key={idx}
                            style={{
                              padding: '8px 10px',
                              borderRadius: '6px',
                              border: '1px solid var(--line)',
                              background: 'var(--surface-raised)',
                              fontSize: '12px',
                            }}
                          >
                            <div style={{ fontWeight: 600, color: 'var(--text)' }}>{item.subject}</div>
                            <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>
                              From: {item.sender} · {item.attachments?.join(', ')}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div className="connector-tabs">
                    <button
                      type="button"
                      className={`connector-tab ${connectorMode === 'demo' ? 'is-active' : ''}`}
                      onClick={() => setConnectorMode('demo')}
                    >
                      Demo Mailbox (Instant Verification)
                    </button>
                    <button
                      type="button"
                      className={`connector-tab ${connectorMode === 'live' ? 'is-active' : ''}`}
                      onClick={() => setConnectorMode('live')}
                    >
                      Live Mailbox (IMAP)
                    </button>
                  </div>

                  {connectorMode === 'demo' ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                      <p style={{ margin: 0, fontSize: '13px', color: 'var(--muted)', lineHeight: 1.5 }}>
                        Verify the autonomous email ingestion pipeline immediately using sample small-business vendor invoices (Red Bull India, Gujral Enterprises, S.H.C Marketing, procurement emails) without configuring live passwords.
                      </p>

                      <div className="form-group">
                        <label className="form-label">Simulated Small Business Email</label>
                        <input
                          type="text"
                          className="form-input"
                          value={connectorEmail || 'accounts@smallbiz.local'}
                          onChange={(e) => setConnectorEmail(e.target.value)}
                        />
                        <span className="form-hint">
                          Safe sandbox mode: files are indexed in your Supabase RAG workspace and audited dynamically.
                        </span>
                      </div>

                      <button
                        type="button"
                        className="btn-primary"
                        style={{ marginTop: '6px' }}
                        onClick={handleAuthorize}
                        disabled={isAuthorizing}
                      >
                        <Icon name="check" size={15} />
                        <span>{isAuthorizing ? 'Authorizing…' : 'Authorize Demo Mailbox'}</span>
                      </button>
                    </div>
                  ) : (
                    <form onSubmit={handleAuthorize} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                      <p style={{ margin: 0, fontSize: '13px', color: 'var(--muted)', lineHeight: 1.5 }}>
                        Connect your real business email to autonomously pull vendor invoices from your inbox into the financial audit pipeline.
                      </p>

                      <div className="form-group">
                        <label className="form-label">Email Provider</label>
                        <select
                          className="form-select"
                          value={connectorProvider}
                          onChange={(e) => handleProviderSelect(e.target.value)}
                        >
                          <option value="gmail">Google Gmail / Google Workspace (imap.gmail.com)</option>
                          <option value="outlook">Microsoft Outlook 365 (outlook.office365.com)</option>
                          <option value="yahoo">Yahoo Mail (imap.mail.yahoo.com)</option>
                          <option value="custom">Custom IMAP Server</option>
                        </select>
                      </div>

                      <div className="form-group">
                        <label className="form-label">Email Address</label>
                        <input
                          type="email"
                          required
                          className="form-input"
                          placeholder="e.g. billing@mycompany.com"
                          value={connectorEmail}
                          onChange={(e) => setConnectorEmail(e.target.value)}
                        />
                      </div>

                      <div className="form-group">
                        <label className="form-label">App Password</label>
                        <input
                          type="password"
                          required
                          className="form-input"
                          placeholder="16-character App Password"
                          value={connectorPassword}
                          onChange={(e) => setConnectorPassword(e.target.value)}
                        />
                        <span className="form-hint">
                          For security, use an App Password from your Google/Microsoft Account Security dashboard. Credentials are held in-memory session only and never saved to .env.
                        </span>
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '10px' }}>
                        <div className="form-group">
                          <label className="form-label">IMAP Host</label>
                          <input
                            type="text"
                            required
                            className="form-input"
                            value={connectorHost}
                            onChange={(e) => setConnectorHost(e.target.value)}
                          />
                        </div>
                        <div className="form-group">
                          <label className="form-label">Port</label>
                          <input
                            type="number"
                            className="form-input"
                            value={connectorPort}
                            onChange={(e) => setConnectorPort(Number(e.target.value))}
                          />
                        </div>
                      </div>

                      <button
                        type="submit"
                        className="btn-primary"
                        style={{ marginTop: '8px' }}
                        disabled={isAuthorizing}
                      >
                        <Icon name="lock" size={15} />
                        <span>{isAuthorizing ? 'Testing & Authorizing…' : 'Test & Authorize Live Connection'}</span>
                      </button>
                    </form>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Point-of-Sale (POS) & Billing Software Connector Modal */}
      {posModalOpen && (
        <div className="modal-overlay" onClick={() => setPosModalOpen(false)}>
          <div className="modal-dialog" style={{ width: 'min(100%, 600px)' }} onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div className="modal-title">
                <Icon name="cart" size={19} />
                <span>POS & Billing Software Connector</span>
              </div>
              <button
                className="modal-close"
                onClick={() => setPosModalOpen(false)}
                aria-label="Close dialog"
              >
                <Icon name="close" size={16} />
              </button>
            </div>

            <div className="modal-body">
              {posStatus.is_authorized ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div className="connected-card">
                    <div className="connected-card-header">
                      <strong style={{ fontSize: '13px' }}>POS Software Connected</strong>
                      <span className="connected-status-pill">
                        <span className="connector-dot is-active" />
                        Authorized
                      </span>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', fontSize: '12px' }}>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>Store Name</div>
                        <strong style={{ color: 'var(--text)' }}>{posStatus.store_name}</strong>
                      </div>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>GSTIN</div>
                        <strong style={{ color: 'var(--text)' }}>{posStatus.gstin}</strong>
                      </div>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>Mode</div>
                        <strong style={{ color: 'var(--text)' }}>
                          {posStatus.mode === 'demo' ? 'Simulated POS Register' : `Live Webhook (${posStatus.provider})`}
                        </strong>
                      </div>
                      <div>
                        <div style={{ color: 'var(--quiet)', fontSize: '11px' }}>Total Sales Recorded</div>
                        <strong style={{ color: 'var(--text)' }}>₹{posStatus.total_sales_amount} ({posStatus.bills_synced_count} bills)</strong>
                      </div>
                    </div>

                    {posSessionApiKey && (
                      <div style={{ padding: '8px', borderRadius: '6px', background: 'var(--surface-raised)', fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                        <span style={{ color: 'var(--quiet)' }}>Live POS Webhook URL:</span>
                        <code style={{ color: 'var(--accent)', fontSize: '11px' }}>http://127.0.0.1:8000/api/connectors/pos/webhook</code>
                        <span style={{ color: 'var(--quiet)', marginTop: '2px' }}>Session Header Token:</span>
                        <code style={{ color: 'var(--text)', fontSize: '10px', wordBreak: 'break-all' }}>X-POS-API-KEY: {posSessionApiKey}</code>
                      </div>
                    )}
                  </div>

                  {/* Interactive Barcode Scanner Simulation (Option B) */}
                  <div style={{ padding: '14px', borderRadius: '8px', border: '1px solid var(--line)', background: 'var(--surface-raised)', display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, fontSize: '13px' }}>
                      <Icon name="barcode" size={16} />
                      <span>Interactive Barcode Scanner Simulation</span>
                    </div>
                    <p style={{ margin: 0, fontSize: '12px', color: 'var(--muted)', lineHeight: 1.45 }}>
                      Select a fast-moving grocery/retail item barcode to simulate customer checkout and generate an official GST cash memo into Supabase.
                    </p>

                    <div className="form-group">
                      <label className="form-label">Scanned Item Barcode</label>
                      <select
                        className="form-select"
                        value={selectedBarcode}
                        onChange={(e) => setSelectedBarcode(e.target.value)}
                      >
                        {(posStatus.catalog_items || []).map((it) => (
                          <option key={it.barcode} value={it.barcode}>
                            {it.name} · [{it.barcode}] · ₹{it.mrp} ({it.gst_rate}% GST)
                          </option>
                        ))}
                      </select>
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                      <div className="form-group">
                        <label className="form-label">Quantity</label>
                        <select
                          className="form-select"
                          value={scanQty}
                          onChange={(e) => setScanQty(Number(e.target.value))}
                        >
                          <option value={1}>1 unit</option>
                          <option value={2}>2 units</option>
                          <option value={3}>3 units</option>
                          <option value={5}>5 units</option>
                          <option value={10}>10 units (Bulk)</option>
                        </select>
                      </div>
                      <div className="form-group">
                        <label className="form-label">Customer Payment</label>
                        <select
                          className="form-select"
                          value={scanPaymentMethod}
                          onChange={(e) => setScanPaymentMethod(e.target.value)}
                        >
                          <option value="UPI">UPI (PhonePe / GPay / Paytm)</option>
                          <option value="Cash">Cash at Counter</option>
                          <option value="Card">Debit / Credit Card</option>
                        </select>
                      </div>
                    </div>

                    <button
                      type="button"
                      className="btn-primary"
                      onClick={handleScanBarcode}
                      disabled={isScanningBarcode}
                    >
                      <Icon name="barcode" size={15} />
                      <span>{isScanningBarcode ? 'Scanning & Ingesting…' : 'Scan Barcode & Generate Bill'}</span>
                    </button>
                  </div>

                  {/* Batch Daily Sales Sync */}
                  <div style={{ display: 'flex', gap: '10px' }}>
                    <button
                      type="button"
                      className="btn-primary"
                      style={{ flex: 1, background: '#2563eb' }}
                      onClick={handleSyncPOSBatch}
                      disabled={isSyncingPOSBatch}
                    >
                      <Icon name="refresh" size={15} />
                      <span>{isSyncingPOSBatch ? 'Syncing Register…' : 'Sync Daily Sales Register'}</span>
                    </button>
                    <button
                      type="button"
                      className="btn-danger"
                      onClick={handleRevokePOS}
                    >
                      Disconnect POS
                    </button>
                  </div>

                  {/* Recently Ingested Bills */}
                  {posStatus.recent_bills && posStatus.recent_bills.length > 0 && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                      <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--quiet)', letterSpacing: '0.5px' }}>
                        RECENT CHECKOUT BILLS
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                        {posStatus.recent_bills.map((b, idx) => (
                          <div
                            key={idx}
                            style={{
                              padding: '8px 10px',
                              borderRadius: '6px',
                              border: '1px solid var(--line)',
                              background: 'var(--surface-raised)',
                              fontSize: '12px',
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'center',
                            }}
                          >
                            <div>
                              <strong style={{ color: 'var(--text)' }}>{b.bill_number}</strong>
                              <span style={{ fontSize: '11px', color: 'var(--quiet)', marginLeft: '6px' }}>
                                ({b.payment_method} · {b.timestamp})
                              </span>
                            </div>
                            <span style={{ fontWeight: 600, color: '#76a47b' }}>
                              ₹{b.grand_total}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                  <div className="connector-tabs">
                    <button
                      type="button"
                      className={`connector-tab ${posMode === 'demo' ? 'is-active' : ''}`}
                      onClick={() => setPosMode('demo')}
                    >
                      Demo POS (Interactive Barcode & Register)
                    </button>
                    <button
                      type="button"
                      className={`connector-tab ${posMode === 'live' ? 'is-active' : ''}`}
                      onClick={() => setPosMode('live')}
                    >
                      Live POS / Webhook (Vyapar, Tally, Zoho)
                    </button>
                  </div>

                  {posMode === 'demo' ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                      <p style={{ margin: 0, fontSize: '13px', color: 'var(--muted)', lineHeight: 1.5 }}>
                        Instantly test barcode scanning, automatic GST cash memo generation, and sales-to-bank reconciliation without requiring hardware scanners or third-party POS accounts.
                      </p>

                      <div className="form-group">
                        <label className="form-label">Store / Business Name</label>
                        <input
                          type="text"
                          className="form-input"
                          value={posStoreName}
                          onChange={(e) => setPosStoreName(e.target.value)}
                        />
                      </div>

                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                        <div className="form-group">
                          <label className="form-label">Store GSTIN</label>
                          <input
                            type="text"
                            className="form-input"
                            value={posGstin}
                            onChange={(e) => setPosGstin(e.target.value)}
                          />
                        </div>
                        <div className="form-group">
                          <label className="form-label">Terminal ID</label>
                          <input
                            type="text"
                            className="form-input"
                            value={posTerminalId}
                            onChange={(e) => setPosTerminalId(e.target.value)}
                          />
                        </div>
                      </div>

                      <button
                        type="button"
                        className="btn-primary"
                        style={{ marginTop: '6px' }}
                        onClick={handleAuthorizePOS}
                        disabled={isAuthorizingPOS}
                      >
                        <Icon name="check" size={15} />
                        <span>{isAuthorizingPOS ? 'Authorizing…' : 'Authorize Demo POS Register'}</span>
                      </button>
                    </div>
                  ) : (
                    <form onSubmit={handleAuthorizePOS} style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                      <p style={{ margin: 0, fontSize: '13px', color: 'var(--muted)', lineHeight: 1.5 }}>
                        Connect your billing software via secure webhook. Whenever a customer checkout bill is finalized in Vyapar, Tally, or your barcode scanner, it is pushed directly to the financial audit engine.
                      </p>

                      <div className="form-group">
                        <label className="form-label">Billing / POS Software</label>
                        <select
                          className="form-select"
                          value={posProvider}
                          onChange={(e) => setPosProvider(e.target.value)}
                        >
                          <option value="vyapar">Vyapar Billing & Accounting (Desktop / Mobile)</option>
                          <option value="tally">Tally Prime (XML / JSON Bridge)</option>
                          <option value="zoho_books">Zoho Books POS</option>
                          <option value="petpooja">Petpooja Restaurant & Retail POS</option>
                          <option value="custom_pos">Custom Barcode Scanner / POS Register</option>
                        </select>
                      </div>

                      <div className="form-group">
                        <label className="form-label">Store Name</label>
                        <input
                          type="text"
                          required
                          className="form-input"
                          value={posStoreName}
                          onChange={(e) => setPosStoreName(e.target.value)}
                        />
                      </div>

                      <div className="form-group">
                        <label className="form-label">Store GSTIN</label>
                        <input
                          type="text"
                          required
                          className="form-input"
                          value={posGstin}
                          onChange={(e) => setPosGstin(e.target.value)}
                        />
                      </div>

                      <button
                        type="submit"
                        className="btn-primary"
                        style={{ marginTop: '8px' }}
                        disabled={isAuthorizingPOS}
                      >
                        <Icon name="lock" size={15} />
                        <span>{isAuthorizingPOS ? 'Authorizing…' : 'Authorize & Generate Webhook Endpoint'}</span>
                      </button>
                    </form>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {notice && <div className="toast" role="status">{notice}</div>}
    </div>
  )
}

export default App
