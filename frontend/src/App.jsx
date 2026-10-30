import { useEffect, useRef, useState } from 'react'

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
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem('ledgerline-react-theme') || 'dark'
    } catch {
      return 'dark'
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
    document.querySelector('meta[name="theme-color"]').content = theme === 'dark' ? '#171b18' : '#f3f5f1'
    try {
      localStorage.setItem('ledgerline-react-theme', theme)
    } catch {
      // The theme still works if storage is unavailable.
    }
  }, [theme])

  useEffect(() => {
    conversationEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  useEffect(() => () => window.clearTimeout(noticeTimer.current), [])

  function showNotice(message) {
    setNotice(message)
    window.clearTimeout(noticeTimer.current)
    noticeTimer.current = window.setTimeout(() => setNotice(''), 2400)
  }

  function startNewChat() {
    if (isBusy) return
    setMessages([])
    setDraft('')
    setAttachedFiles([])
    setActiveChat('')
    setSidebarOpen(false)
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

      const response = await fetch('/api/query', { method: 'POST', body })
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
    setSidebarOpen(false)
    sendMessage(chat.prompt)
  }

  function handleFiles(event) {
    const files = Array.from(event.target.files || [])
    if (!files.length) return
    setAttachedFiles((current) => [...current, ...files])
    showNotice(`${files.length} file${files.length === 1 ? '' : 's'} added to this draft`)
    event.target.value = ''
  }

  return (
    <div className={`app-shell theme-${theme}`}>
      <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
        <div className="brand-row">
          <div className="brand"><span className="brand-mark">L</span><span>ledgerline</span></div>
          <button className="icon-button sidebar-close" onClick={() => setSidebarOpen(false)} aria-label="Close menu"><Icon name="close" /></button>
          <button className="icon-button desktop-add" onClick={startNewChat} aria-label="Start a new conversation"><Icon name="plus" /></button>
        </div>

        <button className="new-chat-button" onClick={startNewChat} disabled={isBusy}><Icon name="plus" size={16} /> New conversation</button>

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
          <div className="profile-row"><span className="profile-avatar">L</span><span className="profile-copy"><strong>Document workspace</strong><small>Supabase RAG</small></span><button className="profile-more" onClick={() => showNotice('Search uses your configured Supabase business workspace.')} aria-label="Workspace information">···</button></div>
        </div>
      </aside>

      {sidebarOpen && <button className="mobile-backdrop" onClick={() => setSidebarOpen(false)} aria-label="Close navigation" />}

      <main className="main-panel">
        <header className="topbar">
          <div className="case-heading">
            <button className="icon-button mobile-menu-button" onClick={() => setSidebarOpen(true)} aria-label="Open menu"><Icon name="menu" /></button>
            <div><div className="case-title">Financial workspace <span>/</span> Document search</div><div className="case-subtitle">RAG retrieval · 4-tier LLM cascade decision maker</div></div>
          </div>
          <div className="topbar-actions">
            <span className="sample-label">LIVE RETRIEVAL</span>
            <button className="theme-button" onClick={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}><Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} /><span>{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span></button>
            <button className="case-details" onClick={() => showNotice('Retrieved chunks and LLM cascade audit decisions are shown below.')}>Search details <span>⌄</span></button>
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
            <div className="composer-bottom"><button type="button" className="attach-button" disabled={isBusy} onClick={() => fileInput.current?.click()}><Icon name="attach" size={16} /><span>Attach files</span></button><input ref={fileInput} type="file" hidden multiple accept=".pdf,.png,.jpg,.jpeg,.webp,.tif,.tiff,.txt,.xlsx,.xlsm,.wav,.mp3,.ogg,.flac,.aiff,.aif" onChange={handleFiles} /><div className="composer-controls"><span className="enter-hint">Enter to send</span><button className="send-button" type="submit" disabled={!draft.trim() || isBusy}><span>{isBusy ? 'Working…' : 'Send'}</span><Icon name="send" size={14} /></button></div></div>
          </form>
          <div className="composer-note">Files are parsed and saved · Queries are audited dynamically by the LLM cascade decision maker</div>
        </div>
      </main>

      {notice && <div className="toast" role="status">{notice}</div>}
    </div>
  )
}

export default App
