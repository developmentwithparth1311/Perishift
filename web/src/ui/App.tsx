import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, AlertCircle, ArrowDownToLine, ArrowRight, CheckCircle2, CircleHelp,
  Clock3, KeyRound, LoaderCircle, PackageCheck, Plus, RefreshCw, ShieldCheck,
  Truck, Wifi, WifiOff,
} from 'lucide-react'

type ItemClass = 'HOT_COOKED_MEAL' | 'RAW_RATION_KIT' | 'POWDER_FORMULA' | 'COLD_CHAIN_MEDICINE'
type QuantityUnit = 'MEAL' | 'KIT' | 'PACKAGE'
type BatchBalance = {
  batch_id: string; item_class: ItemClass; unit: QuantityUnit; total_qty: number
  available_qty: number; reserved_qty: number; carried_qty: number; delivered_qty: number
  disposed_qty: number; safe_by: string; deliver_by: string; version: number
}
type DemandSnapshot = {
  demand_id: string; shelter_id: string; item_class: ItemClass; unit: QuantityUnit
  required_qty: number; received_qty: number; reserved_qty: number; transit_qty: number
  uncommitted_need: number; intake_open: boolean; version: number
  service_window_start: string | null; service_window_end: string | null
}

const itemNames: Record<ItemClass, string> = {
  HOT_COOKED_MEAL: 'Hot cooked meal', RAW_RATION_KIT: 'Raw ration kit',
  POWDER_FORMULA: 'Powder formula', COLD_CHAIN_MEDICINE: 'Cold-chain medicine',
}
const unitByItem: Record<ItemClass, QuantityUnit> = {
  HOT_COOKED_MEAL: 'MEAL', RAW_RATION_KIT: 'KIT',
  POWDER_FORMULA: 'PACKAGE', COLD_CHAIN_MEDICINE: 'PACKAGE',
}
const apiDefault = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : 'The request failed unexpectedly.'
}

async function request<T>(url: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(url, {
    ...init,
    headers,
  })
  const body = await response.json().catch(() => null) as unknown
  if (!response.ok) {
    const detail = body && typeof body === 'object' ? body as Record<string, unknown> : {}
    const error = detail.error && typeof detail.error === 'object' ? detail.error as Record<string, unknown> : {}
    const message = typeof error.message === 'string' ? error.message
      : typeof detail.detail === 'string' ? detail.detail
        : `API returned HTTP ${response.status}.`
    throw new Error(message)
  }
  return body as T
}

function formatDate(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? 'Unknown' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function App() {
  const [apiBase, setApiBase] = useState(apiDefault)
  const [token, setToken] = useState('')
  const [scenarioId, setScenarioId] = useState('')
  const [donorId, setDonorId] = useState('')
  const [batches, setBatches] = useState<BatchBalance[]>([])
  const [demands, setDemands] = useState<DemandSnapshot[]>([])
  const [batchError, setBatchError] = useState('')
  const [demandError, setDemandError] = useState('')
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [notice, setNotice] = useState('')
  const [itemClass, setItemClass] = useState<ItemClass>('HOT_COOKED_MEAL')
  const [quantity, setQuantity] = useState('')
  const [readyAt, setReadyAt] = useState('')
  const [safeBy, setSafeBy] = useState('')
  const [bufferSeconds, setBufferSeconds] = useState('600')
  const batchAttempt = useRef<{ signature: string; key: string } | null>(null)

  const normalizedBase = useMemo(() => apiBase.trim().replace(/\/+$/, ''), [apiBase])
  const configured = Boolean(normalizedBase && token.trim())
  const refresh = useCallback(async () => {
    if (!configured) {
      setNotice('Enter the API URL and a bearer token to load live records.')
      return
    }
    setLoading(true)
    setNotice('')
    const batchQuery = scenarioId.trim() ? `?scenario_id=${encodeURIComponent(scenarioId.trim())}` : ''
    const [batchResult, demandResult] = await Promise.allSettled([
      request<BatchBalance[]>(`${normalizedBase}/api/v1/batches${batchQuery}`, token.trim()),
      request<DemandSnapshot[]>(`${normalizedBase}/demands`, token.trim()),
    ])
    if (batchResult.status === 'fulfilled' && Array.isArray(batchResult.value)) {
      setBatches(batchResult.value)
      setBatchError('')
    } else {
      setBatches([])
      setBatchError(batchResult.status === 'rejected' ? readableError(batchResult.reason) : 'Batch API response was not a list.')
    }
    if (demandResult.status === 'fulfilled' && Array.isArray(demandResult.value)) {
      setDemands(demandResult.value)
      setDemandError('')
    } else {
      setDemands([])
      setDemandError(demandResult.status === 'rejected' ? readableError(demandResult.reason) : 'Demand API response was not a list.')
    }
    setLastRefresh(new Date())
    setLoading(false)
  }, [configured, normalizedBase, scenarioId, token])

  useEffect(() => {
    if (configured) void refresh()
    else {
      setBatches([])
      setDemands([])
      setBatchError('')
      setDemandError('')
      setLastRefresh(null)
    }
  }, [configured, refresh])

  const totals = useMemo(() => batches.reduce((value, batch) => ({
    available: value.available + batch.available_qty,
    carried: value.carried + batch.carried_qty,
    delivered: value.delivered + batch.delivered_qty,
  }), { available: 0, carried: 0, delivered: 0 }), [batches])
  const needTotal = useMemo(() => demands.reduce((sum, demand) => sum + demand.uncommitted_need, 0), [demands])

  async function submitBatch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!configured) { setNotice('Connect to the API before creating a donation.'); return }
    if (!donorId.trim()) { setNotice('Enter the donor UUID associated with this token.'); return }
    const qty = Number(quantity)
    const buffer = Number(bufferSeconds)
    const readyDate = new Date(readyAt)
    const safeDate = new Date(safeBy)
    if (!Number.isInteger(qty) || qty < 1) { setNotice('Quantity must be a positive whole number.'); return }
    if (!Number.isInteger(buffer) || buffer < 0) { setNotice('Serving buffer must be a non-negative whole number.'); return }
    if (!Number.isFinite(readyDate.valueOf()) || !Number.isFinite(safeDate.valueOf()) || readyDate >= safeDate) {
      setNotice('Set valid times and make the safe-by time later than ready-at.'); return
    }
    const payload = {
      scenario_id: scenarioId.trim() || null,
      donor_id: donorId.trim(), item_class: itemClass, unit: unitByItem[itemClass], qty,
      ready_at: readyDate.toISOString(), declared_safe_by: safeDate.toISOString(),
      serving_buffer_seconds: buffer,
    }
    const signature = JSON.stringify(payload)
    if (batchAttempt.current?.signature !== signature) {
      batchAttempt.current = { signature, key: crypto.randomUUID() }
    }
    setCreating(true)
    setNotice('')
    try {
      await request<BatchBalance>(`${normalizedBase}/api/v1/batches`, token.trim(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': batchAttempt.current.key },
        body: JSON.stringify(payload),
      })
      batchAttempt.current = null
      setQuantity('')
      await refresh()
      setNotice('Donation recorded by the API.')
    } catch (error) {
      setNotice(`Donation was not recorded: ${readableError(error)}`)
    } finally {
      setCreating(false)
    }
  }

  return <main className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Activity size={20} /></span><span>PeriShift</span></div>
      <div className="workspace-label">OPERATIONS</div>
      <nav aria-label="Main navigation">
        <a className="nav-item active" href="#overview"><Activity size={17} /> Control tower</a>
        <a className="nav-item" href="#donations"><PackageCheck size={17} /> Donations</a>
        <a className="nav-item" href="#demand"><ShieldCheck size={17} /> Shelter demand</a>
        <a className="nav-item" href="#dispatch"><Truck size={17} /> Dispatch workflow</a>
      </nav>
      <div className="sidebar-bottom"><span className={`live-dot ${configured && !batchError ? '' : 'offline'}`} />{configured ? batchError ? 'API needs attention' : 'API configured' : 'Not connected'}<span className="version">MVP</span></div>
    </aside>

    <section className="main-content" id="overview">
      <header className="topbar"><div className="crumb">Operations <span>/</span> Control tower</div><div className={`mode-pill ${configured && lastRefresh && !batchError ? 'connected' : 'disconnected'}`}><i />{!configured ? 'NOT CONNECTED' : loading ? 'CHECKING API' : batchError ? 'API ERROR' : lastRefresh ? 'LIVE API' : 'TOKEN SET'}</div></header>
      <div className="page">
        <div className="page-heading"><div><div className="eyebrow">PERISHABLE AID NETWORK</div><h1>Control tower</h1><p className="subtitle">Live supply and shelter need from the PeriShift API.</p></div><button className="primary-button" onClick={() => void refresh()} disabled={!configured || loading} type="button"><RefreshCw size={15} className={loading ? 'spin' : ''} /> Refresh live data</button></div>

        <section className="connection panel" aria-labelledby="connection-title">
          <div className="panel-heading"><div><h2 id="connection-title"><KeyRound size={15} /> API connection</h2><p>Credentials stay in this page session and are sent only to the configured API.</p></div><span className="connection-state">{lastRefresh && !batchError ? <><Wifi size={14} /> Live data loaded</> : configured ? <><Wifi size={14} /> Token configured</> : <><WifiOff size={14} /> Credentials required</>}</span></div>
          <div className="connection-grid">
            <label>API base URL<input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="http://localhost:8000" autoComplete="url" /></label>
            <label>Bearer JWT<input value={token} onChange={(event) => setToken(event.target.value)} type="password" placeholder="Paste API token" autoComplete="off" /></label>
            <label>Scenario UUID <span className="optional">optional</span><input value={scenarioId} onChange={(event) => setScenarioId(event.target.value)} placeholder="Live scope: leave blank" /></label>
          </div>
          <div className="connection-foot"><span><CircleHelp size={13} /> Batches require a donor/coordinator/judge token. Demands are visible to a shelter token.</span><span>{lastRefresh ? `Last updated ${lastRefresh.toLocaleTimeString()}` : 'No live data loaded'}</span></div>
        </section>

        {notice && <div className={`notice ${notice.startsWith('Donation recorded') ? 'success' : ''}`} role="status"><AlertCircle size={17} />{notice}</div>}

        <div className="section-title"><h2>Network snapshot</h2><span>{loading ? 'Loading from API…' : configured ? 'Source: live API records' : 'Connect to the API to load records'}</span></div>
        <div className="metrics">
          <article className="metric-card"><div className="metric-top">Available supply <PackageCheck size={17} /></div><div className="metric-value">{totals.available.toLocaleString()} <small>units</small></div><div className="metric-foot">Across loaded batches</div></article>
          <article className="metric-card"><div className="metric-top">In rider custody <Truck size={17} /></div><div className="metric-value">{totals.carried.toLocaleString()} <small>units</small></div><div className="metric-foot">Physical cargo reported by API</div></article>
          <article className="metric-card"><div className="metric-top">Delivered <CheckCircle2 size={17} /></div><div className="metric-value">{totals.delivered.toLocaleString()} <small>units</small></div><div className="metric-foot">Confirmed receipts in loaded batches</div></article>
          <article className="metric-card"><div className="metric-top">Uncommitted need <ArrowDownToLine size={17} /></div><div className="metric-value">{needTotal.toLocaleString()} <small>units</small></div><div className="metric-foot">Across visible shelter demands</div></article>
        </div>

        <div className="content-grid">
          <section className="panel" id="donations">
            <div className="panel-heading"><div><h2>Record a donation</h2><p>Create a real batch in the connected API.</p></div><span className="section-icon"><Plus size={16} /></span></div>
            <form className="donation-form" onSubmit={(event) => void submitBatch(event)}>
              <label>Donor UUID<input value={donorId} onChange={(event) => setDonorId(event.target.value)} placeholder="UUID for the donor linked to your token" /></label>
              <div className="form-row"><label>Item class<select value={itemClass} onChange={(event) => setItemClass(event.target.value as ItemClass)}>{Object.entries(itemNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label>Quantity ({unitByItem[itemClass].toLowerCase()})<input type="number" min="1" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} placeholder="0" /></label></div>
              <div className="form-row"><label>Ready at<input type="datetime-local" value={readyAt} onChange={(event) => setReadyAt(event.target.value)} /></label><label>Declared safe by<input type="datetime-local" value={safeBy} onChange={(event) => setSafeBy(event.target.value)} /></label></div>
              <label>Serving buffer <span className="optional">seconds after pickup</span><input type="number" min="0" step="1" value={bufferSeconds} onChange={(event) => setBufferSeconds(event.target.value)} /></label>
              <button className="primary-button submit-button" type="submit" disabled={!configured || creating}>{creating ? <LoaderCircle size={15} className="spin" /> : <Plus size={15} />}{creating ? 'Recording…' : 'Record donation'}</button>
              {!donorId.trim() && <p className="form-hint">Enter the donor UUID belonging to the authenticated user.</p>}
            </form>
          </section>

          <section className="panel" id="demand">
            <div className="panel-heading"><div><h2>Shelter demand</h2><p>Demand visible to the authenticated shelter actor.</p></div><span className="section-icon"><ShieldCheck size={16} /></span></div>
            {demandError ? <ApiError message={demandError} /> : demands.length ? <div className="table-wrap"><table><thead><tr><th>Item</th><th>Need</th><th>State</th></tr></thead><tbody>{demands.map((demand) => <tr key={demand.demand_id}><td><strong>{itemNames[demand.item_class]}</strong><small>{demand.demand_id.slice(0, 8)} · {demand.unit.toLowerCase()}</small></td><td>{demand.uncommitted_need} / {demand.required_qty}</td><td><span className={`status ${demand.intake_open ? 'open' : 'closed'}`}>{demand.intake_open ? 'Open' : 'Closed'}</span></td></tr>)}</tbody></table></div> : <EmptyState loaded={lastRefresh !== null} message="No demand records returned for this actor." />}
          </section>
        </div>

        <section className="panel batches-panel">
          <div className="panel-heading"><div><h2>Donation batches</h2><p>Inventory buckets are read directly from the batch ledger.</p></div><span className="record-count">{batches.length} records</span></div>
          {batchError ? <ApiError message={batchError} /> : batches.length ? <div className="table-wrap"><table><thead><tr><th>Item / batch</th><th>Available</th><th>Reserved</th><th>In custody</th><th>Delivered</th><th>Safe by</th></tr></thead><tbody>{batches.map((batch) => <tr key={batch.batch_id}><td><strong>{itemNames[batch.item_class]}</strong><small>{batch.batch_id.slice(0, 8)} · {batch.total_qty} {batch.unit.toLowerCase()}</small></td><td>{batch.available_qty}</td><td>{batch.reserved_qty}</td><td>{batch.carried_qty}</td><td>{batch.delivered_qty}</td><td>{formatDate(batch.safe_by)}</td></tr>)}</tbody></table></div> : <EmptyState loaded={lastRefresh !== null} message="No batches found in this scope." />}
        </section>

        <section className="panel workflow-panel" id="dispatch">
          <div className="panel-heading"><div><h2>Dispatch workflow</h2><p>Each action will call the backend and respect its state, safety, and lease checks.</p></div><span className="state-label"><i /> ROUTES NOT AVAILABLE</span></div>
          <div className="workflow-steps">{['Offers', 'Lease acceptance', 'Pickup', 'Delivery confirmation'].map((step, index) => <div className="workflow-step" key={step}><span>{String(index + 1).padStart(2, '0')}</span><strong>{step}</strong>{index < 3 && <ArrowRight size={14} />}</div>)}</div>
          <p className="workflow-note"><Clock3 size={14} /> Dispatch endpoints are not yet exposed by the API. This panel will become actionable when those routes are implemented; no simulated actions are sent.</p>
        </section>
        <footer>Operational state is sourced from the API. When a request fails, the error is shown without treating it as a successful action.</footer>
      </div>
    </section>
  </main>
}

function ApiError({ message }: { message: string }) {
  return <div className="api-error"><AlertCircle size={16} /><div><strong>Could not load this resource</strong><span>{message}</span></div></div>
}

function EmptyState({ loaded, message }: { loaded: boolean; message: string }) {
  return <div className="empty-state"><PackageCheck size={21} /><strong>{loaded ? message : 'Connect to load live records'}</strong><span>{loaded ? 'The API returned an empty collection.' : 'Add a bearer token and refresh.'}</span></div>
}

export default App
