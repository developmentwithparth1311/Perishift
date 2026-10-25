import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { LOCAL_DEMO_COORDINATOR_TOKEN } from '../demo'
import DemoRouteMap from './DemoRouteMap'
import {
  Activity, AlertCircle, ArrowDownToLine, ArrowRight, CheckCircle2, CircleHelp,
  Clock3, KeyRound, LoaderCircle, PackageCheck, Play, Plus, RefreshCw, ShieldCheck,
  Truck, Wifi, WifiOff,
} from 'lucide-react'

type ItemClass = 'HOT_COOKED_MEAL' | 'RAW_RATION_KIT' | 'POWDER_FORMULA' | 'COLD_CHAIN_MEDICINE'
type QuantityUnit = 'MEAL' | 'KIT' | 'PACKAGE'
type Role = 'donor' | 'coordinator' | 'rider' | 'shelter'
type Tokens = Record<Role, string>
type ApiConnection = { baseUrl: string; scenarioId: string; tokens: Tokens; applied: boolean }
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
type OfferResponse = { offer_id: string; lot_id: string; rider_id: string; demand_id: string; qty: number; status: string; expires_at: string; version: number; lot_version: number }
type AcceptResponse = { lot_id: string; state: string; version: number; offer_id: string; assignment_id: string; lease_id: string; lease_status: string; lease_expires_at: string; first_progress_by: string; reserved_qty: number }
type CustodyResponse = {
  lot_id: string; lot_state: string; lot_version: number; assignment_id: string
  assignment_status: string; assignment_version: number; picked_qty: number
  accepted_qty: number; rejected_qty: number; carried_qty: number
  risk_flags: string[]; rider_id: string; rider_available: boolean
  inventory: Record<string, number>; demand: Record<string, number>
}
type Workflow = {
  lotId: string; offerId: string; riderId: string; demandId: string; qty: number
  step: 'OFFERED' | 'LEASED' | 'IN_TRANSIT' | 'DELIVERED'
  expectedLotVersion: number; pickedQty: number; acceptedQty: number; rejectedQty: number
  carriedQty: number; leaseExpiresAt?: string
}
type LiveEvent = { id: number; type: string; aggregate_type: string; aggregate_id: string; occurred_at: string; payload: Record<string, unknown> }

const itemNames: Record<ItemClass, string> = {
  HOT_COOKED_MEAL: 'Hot cooked meal', RAW_RATION_KIT: 'Raw ration kit',
  POWDER_FORMULA: 'Powder formula', COLD_CHAIN_MEDICINE: 'Cold-chain medicine',
}
const unitByItem: Record<ItemClass, QuantityUnit> = {
  HOT_COOKED_MEAL: 'MEAL', RAW_RATION_KIT: 'KIT',
  POWDER_FORMULA: 'PACKAGE', COLD_CHAIN_MEDICINE: 'PACKAGE',
}
const apiDefault = import.meta.env.VITE_API_BASE_URL || ''
const blankTokens: Tokens = { donor: '', coordinator: '', rider: '', shelter: '' }

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request failed unexpectedly.'
}

type ActiveDispatch = {
  lot_id: string; batch_id: string; offer_id: string; rider_id: string; demand_id: string
  qty: number; state: Workflow['step']; lot_version: number; rider_version: number
  picked_qty: number; accepted_qty: number; rejected_qty: number; carried_qty: number
  lease_expires_at: string | null
}

type DemoResetResponse = {
  scenario: Record<string, unknown>; bearer_tokens: Tokens; token_expires_at: string | null
  rider_version?: number; active_dispatch?: ActiveDispatch | null
}

async function apiRequest<T>(url: string, token: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Accept', 'application/json')
  headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(url, { ...init, headers })
  const body = await response.json().catch(() => null) as unknown
  if (!response.ok) {
    const detail = body && typeof body === 'object' ? body as Record<string, unknown> : {}
    const nested = detail.error && typeof detail.error === 'object' ? detail.error as Record<string, unknown> : {}
    const httpDetail = detail.detail && typeof detail.detail === 'object' ? detail.detail as Record<string, unknown> : {}
    const message = typeof nested.message === 'string' ? nested.message
      : typeof httpDetail.message === 'string' ? httpDetail.message
        : typeof detail.detail === 'string' ? detail.detail : `API returned HTTP ${response.status}.`
    throw new Error(message)
  }
  return body as T
}

function formatDate(value: string | undefined): string {
  if (!value) return 'Unknown'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? 'Unknown' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}

function randomKey(): string {
  return crypto.randomUUID()
}

function App() {
  const [activeSection, setActiveSection] = useState('overview')
  const [apiBase, setApiBase] = useState(apiDefault)
  const [tokens, setTokens] = useState<Tokens>(blankTokens)
  const [connection, setConnection] = useState<ApiConnection>({ baseUrl: '', scenarioId: '', tokens: blankTokens, applied: false })
  const [scenarioId, setScenarioId] = useState('')
  const [scenarioTime, setScenarioTime] = useState('')
  const [donorId, setDonorId] = useState('')
  const [riderId, setRiderId] = useState('')
  const [riderVersion, setRiderVersion] = useState('0')
  const [seedJson, setSeedJson] = useState('')
  const [batches, setBatches] = useState<BatchBalance[]>([])
  const [demands, setDemands] = useState<DemandSnapshot[]>([])
  const [batchError, setBatchError] = useState('')
  const [demandError, setDemandError] = useState('')
  const [loading, setLoading] = useState(false)
  const [connectingDemo, setConnectingDemo] = useState(false)
  const [creating, setCreating] = useState(false)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [notice, setNotice] = useState('')
  const [itemClass, setItemClass] = useState<ItemClass>('HOT_COOKED_MEAL')
  const [quantity, setQuantity] = useState('')
  const [readyAt, setReadyAt] = useState('')
  const [safeBy, setSafeBy] = useState('')
  const [bufferSeconds, setBufferSeconds] = useState('600')
  const [selectedBatchId, setSelectedBatchId] = useState('')
  const [selectedDemandId, setSelectedDemandId] = useState('')
  const [offerQuantity, setOfferQuantity] = useState('')
  const [pickedQuantity, setPickedQuantity] = useState('')
  const [acceptedQuantity, setAcceptedQuantity] = useState('')
  const [rejectedQuantity, setRejectedQuantity] = useState('0')
  const [workflow, setWorkflow] = useState<Workflow | null>(null)
  const [events, setEvents] = useState<LiveEvent[]>([])
  const [streamState, setStreamState] = useState('Waiting for credentials')
  const batchAttempt = useRef<{ signature: string; key: string } | null>(null)
  const commandKeys = useRef(new Map<string, { signature: string; key: string }>())
  const eventCursor = useRef(0)

  useEffect(() => {
    const syncActiveSection = () => setActiveSection(window.location.hash.slice(1) || 'overview')
    window.addEventListener('hashchange', syncActiveSection)
    window.addEventListener('popstate', syncActiveSection)
    return () => {
      window.removeEventListener('hashchange', syncActiveSection)
      window.removeEventListener('popstate', syncActiveSection)
    }
  }, [])

  const base = useMemo(() => connection.baseUrl.trim().replace(/\/+$/, ''), [connection.baseUrl])
  const batchToken = connection.tokens.coordinator.trim() || connection.tokens.donor.trim()
  const commitToken = connection.tokens.coordinator.trim() || connection.tokens.donor.trim()
  const configured = connection.applied && Object.values(connection.tokens).some((value) => value.trim().length > 0)
  const draftChanged = connection.applied && (
    apiBase.trim() !== connection.baseUrl || scenarioId.trim() !== connection.scenarioId ||
    (Object.keys(blankTokens) as Role[]).some((role) => tokens[role] !== connection.tokens[role])
  )
  const activeBatch = useMemo(() => batches.find((row) => row.batch_id === selectedBatchId), [batches, selectedBatchId])
  const activeDemand = useMemo(() => demands.find((row) => row.demand_id === selectedDemandId), [demands, selectedDemandId])

  const refresh = useCallback(async () => {
    if (!configured) {
      setNotice('Apply at least one role token before loading live data.')
      return
    }
    setLoading(true)
    setNotice('')
    const query = connection.scenarioId.trim() ? `?scenario_id=${encodeURIComponent(connection.scenarioId.trim())}` : ''
    const batchPromise = batchToken
      ? apiRequest<BatchBalance[]>(`${base}/api/v1/batches${query}`, batchToken)
      : Promise.reject(new Error('Add a coordinator or donor token to load batches.'))
    const demandPromise = connection.tokens.shelter.trim()
      ? apiRequest<DemandSnapshot[]>(`${base}/demands`, connection.tokens.shelter.trim())
      : Promise.reject(new Error('Add the shelter bearer token to load demand.'))
    const [batchResult, demandResult] = await Promise.allSettled([batchPromise, demandPromise])
    if (batchResult.status === 'fulfilled' && Array.isArray(batchResult.value)) {
      setBatches(batchResult.value)
      setBatchError('')
      setSelectedBatchId((current) => !current || !batchResult.value.some((item) => item.batch_id === current) ? batchResult.value[0]?.batch_id ?? '' : current)
    } else {
      setBatches([])
      setSelectedBatchId('')
      setBatchError(batchResult.status === 'rejected' ? errorMessage(batchResult.reason) : 'Batch API response was not a list.')
    }
    if (demandResult.status === 'fulfilled' && Array.isArray(demandResult.value)) {
      setDemands(demandResult.value)
      setDemandError('')
      setSelectedDemandId((current) => !current || !demandResult.value.some((item) => item.demand_id === current) ? demandResult.value[0]?.demand_id ?? '' : current)
    } else {
      setDemands([])
      setSelectedDemandId('')
      setDemandError(demandResult.status === 'rejected'
        ? errorMessage(demandResult.reason)
        : 'Demand API response was not a list.')
    }
    setLastRefresh(new Date())
    setLoading(false)
  }, [base, batchToken, configured, connection.scenarioId, connection.tokens.shelter])

  useEffect(() => {
    if (configured) void refresh()
    else {
      setBatches([]); setDemands([]); setBatchError(''); setDemandError(''); setLastRefresh(null)
    }
  }, [configured, refresh])

  useEffect(() => {
    const controller = new AbortController()
    void connectLocalDemo(controller.signal)
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const streamToken = (connection.tokens.coordinator || connection.tokens.shelter || connection.tokens.rider || connection.tokens.donor).trim()
    if (!configured || !streamToken) { setStreamState('Waiting for credentials'); return }
    eventCursor.current = 0
    setEvents([])
    const controller = new AbortController()
    let mounted = true
    const pause = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms))

    const connect = async () => {
      const decoder = new TextDecoder()
      let retrySeconds = 1
      while (!controller.signal.aborted) {
        try {
          const cursor = eventCursor.current ? `?after_id=${eventCursor.current}` : ''
          const response = await fetch(`${base}/api/v1/events/stream${cursor}`, {
            headers: { Accept: 'text/event-stream', Authorization: `Bearer ${streamToken}` },
            signal: controller.signal,
          })
          if (!response.ok || !response.body) throw new Error(`Event stream returned HTTP ${response.status}.`)
          if (mounted) setStreamState('Live event stream connected')
          retrySeconds = 1
          const reader = response.body.getReader()
          let pending = ''
          while (!controller.signal.aborted) {
            const { value, done } = await reader.read()
            if (done) break
            pending += decoder.decode(value, { stream: true })
            const frames = pending.split(/\r?\n\r?\n/)
            pending = frames.pop() ?? ''
            for (const frame of frames) {
              const data = frame.split(/\r?\n/).find((line) => line.startsWith('data:'))?.slice(5).trim()
              if (!data) continue
              try {
                const event = JSON.parse(data) as LiveEvent
                if (typeof event.id !== 'number' || typeof event.type !== 'string') continue
                eventCursor.current = Math.max(eventCursor.current, event.id)
                if (mounted) setEvents((current) => [event, ...current.filter((row) => row.id !== event.id)].slice(0, 15))
              } catch { /* Ignore malformed frames and continue the live stream. */ }
            }
          }
        } catch (error) {
          if (controller.signal.aborted) break
          if (mounted) setStreamState(errorMessage(error))
        }
        await pause(retrySeconds * 1000)
        retrySeconds = Math.min(15, retrySeconds * 2)
      }
    }
    void connect()
    return () => { mounted = false; controller.abort() }
  }, [base, configured, connection.tokens.coordinator, connection.tokens.donor, connection.tokens.rider, connection.tokens.shelter])

  const totals = useMemo(() => batches.reduce((value, batch) => ({
    available: value.available + batch.available_qty,
    carried: value.carried + batch.carried_qty,
    delivered: value.delivered + batch.delivered_qty,
  }), { available: 0, carried: 0, delivered: 0 }), [batches])
  const needTotal = useMemo(() => demands.reduce((sum, demand) => sum + demand.uncommitted_need, 0), [demands])

  function updateToken(role: Role, value: string) { setTokens((current) => ({ ...current, [role]: value })) }

  function connectWithDraft() {
    setConnection({ baseUrl: apiBase.trim().replace(/\/+$/, ''), scenarioId: scenarioId.trim(), tokens: { ...tokens }, applied: true })
    setNotice('Connecting with the configured credentials…')
  }

  function applySeedResult(seed: Record<string, unknown>) {
      const tokenSet = seed.bearer_tokens as Partial<Tokens> | undefined
      if (!seed.scenario_id || !seed.donor_id || !seed.rider_id || !seed.batch_id || !seed.demand_id || !tokenSet?.coordinator || !tokenSet.rider || !tokenSet.shelter) {
        throw new Error('JSON must include scenario/resource IDs and coordinator, rider, and shelter bearer tokens.')
      }
      setScenarioId(String(seed.scenario_id)); setScenarioTime(typeof seed.scenario_time === 'string' ? seed.scenario_time : ''); setDonorId(String(seed.donor_id)); setRiderId(String(seed.rider_id))
      const seededTokens = { donor: String(tokenSet.donor ?? ''), coordinator: String(tokenSet.coordinator), rider: String(tokenSet.rider), shelter: String(tokenSet.shelter) }
      setTokens(seededTokens)
      setConnection({ baseUrl: apiBase.trim().replace(/\/+$/, ''), scenarioId: String(seed.scenario_id), tokens: seededTokens, applied: true })
      setRiderVersion(String(typeof seed.rider_version === 'number' ? seed.rider_version : 0)); setSelectedBatchId(String(seed.batch_id)); setSelectedDemandId(String(seed.demand_id))
      setOfferQuantity(String(seed.quantity ?? 1)); setWorkflow(null); setEvents([]); eventCursor.current = 0
      setBatches([]); setDemands([]); setBatchError(''); setDemandError(''); setLastRefresh(null)
      setPickedQuantity(''); setAcceptedQuantity(''); setRejectedQuantity('0')
      setQuantity(''); setReadyAt(''); setSafeBy('')
      batchAttempt.current = null; commandKeys.current.clear()
      setSeedJson('')
  }

  function loadSeedResult(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    try {
      applySeedResult(JSON.parse(seedJson) as Record<string, unknown>)
      setNotice('Seeded scenario IDs and role tokens loaded from your actual seed command output.')
    } catch (error) { setNotice(`Seed JSON was not loaded: ${errorMessage(error)}`) }
  }

  function applyActiveDispatch(active: ActiveDispatch) {
    setSelectedBatchId(active.batch_id); setSelectedDemandId(active.demand_id)
    setRiderVersion(String(active.rider_version)); setOfferQuantity(String(active.qty))
    setPickedQuantity(String(active.qty)); setAcceptedQuantity(String(active.carried_qty || active.qty)); setRejectedQuantity('0')
    setWorkflow({ lotId: active.lot_id, offerId: active.offer_id, riderId: active.rider_id, demandId: active.demand_id, qty: active.qty, step: active.state, expectedLotVersion: active.lot_version, pickedQty: active.picked_qty, acceptedQty: active.accepted_qty, rejectedQty: active.rejected_qty, carriedQty: active.carried_qty, leaseExpiresAt: active.lease_expires_at ?? undefined })
  }

  async function connectLocalDemo(signal?: AbortSignal) {
    setConnectingDemo(true)
    try {
      const target = apiBase.trim().replace(/\/+$/, '')
      const result = await apiRequest<DemoResetResponse>(`${target}/api/v1/demo/scenarios/connect`, LOCAL_DEMO_COORDINATOR_TOKEN, { method: 'POST', signal })
      if (signal?.aborted) return
      applySeedResult({ ...result.scenario, bearer_tokens: result.bearer_tokens, rider_version: result.rider_version })
      if (result.active_dispatch) applyActiveDispatch(result.active_dispatch)
      setNotice(result.active_dispatch ? 'Demo reconnected and your active delivery restored.' : 'Local demo connected automatically. Start fresh demo to begin a new run.')
    } catch (error) {
      if (!signal?.aborted) setNotice(`Could not connect to the local demo: ${errorMessage(error)}`)
    } finally { if (!signal?.aborted) setConnectingDemo(false) }
  }

  async function startFreshDemo() {
    const token = connection.tokens.coordinator.trim()
    if (!configured || !token || !connection.scenarioId) { setNotice('Load a seeded scenario with coordinator credentials first.'); return }
    setCreating(true); setNotice('')
    try {
      const result = await apiRequest<DemoResetResponse>(`${base}/api/v1/demo/scenarios/${encodeURIComponent(connection.scenarioId)}/reset`, token, idempotentInit(`reset:${connection.scenarioId}`, {}))
      applySeedResult({ ...result.scenario, bearer_tokens: result.bearer_tokens })
      setNotice('Fresh demo created with 20 available meals and shelter demand for 20. Publish an offer to begin. Previous runs remain saved.')
    } catch (error) { setNotice(`Could not start a fresh demo: ${errorMessage(error)}`) }
    finally { setCreating(false) }
  }

  async function submitBatch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!configured || !connection.tokens.donor.trim()) { setNotice('Connect with a donor bearer token first.'); return }
    if (!donorId.trim()) { setNotice('Enter the donor UUID associated with this token.'); return }
    const qty = Number(quantity); const buffer = Number(bufferSeconds)
    const readyDate = new Date(readyAt); const safeDate = new Date(safeBy)
    if (!Number.isInteger(qty) || qty < 1) { setNotice('Quantity must be a positive whole number.'); return }
    if (!Number.isInteger(buffer) || buffer < 0) { setNotice('Serving buffer must be a non-negative whole number.'); return }
    if (!Number.isFinite(readyDate.valueOf()) || !Number.isFinite(safeDate.valueOf()) || readyDate >= safeDate) { setNotice('Set valid times and make safe-by later than ready-at.'); return }
    const payload = { scenario_id: connection.scenarioId.trim() || null, donor_id: donorId.trim(), item_class: itemClass, unit: unitByItem[itemClass], qty, ready_at: readyDate.toISOString(), declared_safe_by: safeDate.toISOString(), serving_buffer_seconds: buffer }
    const signature = JSON.stringify(payload)
    if (batchAttempt.current?.signature !== signature) batchAttempt.current = { signature, key: randomKey() }
    setCreating(true); setNotice('')
    try {
      await apiRequest<BatchBalance>(`${base}/api/v1/batches`, connection.tokens.donor.trim(), { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': batchAttempt.current.key }, body: JSON.stringify(payload) })
      batchAttempt.current = null; setQuantity(''); await refresh(); setNotice('Donation recorded by the API.')
    } catch (error) { setNotice(`Donation was not recorded: ${errorMessage(error)}`) }
    finally { setCreating(false) }
  }

  async function resumeDispatch() {
    const token = connection.tokens.coordinator.trim() || connection.tokens.rider.trim()
    if (!configured || !token || !riderId.trim()) { setNotice('Apply a coordinator or rider token and enter the rider UUID to resume a delivery.'); return }
    setCreating(true); setNotice('')
    try {
      const active = await apiRequest<ActiveDispatch | null>(`${base}/api/v1/dispatch/active?rider_id=${encodeURIComponent(riderId.trim())}`, token)
      if (!active) { setNotice('This rider has no active dispatch to resume.'); return }
      applyActiveDispatch(active)
      await refresh()
      setNotice(active.state === 'LEASED' ? 'Existing delivery restored. Its stock is already reserved. Continue with Confirm pickup.' : `Existing delivery restored: ${active.state.replaceAll('_', ' ')}.`)
    } catch (error) { setNotice(`Could not resume the delivery: ${errorMessage(error)}`) }
    finally { setCreating(false) }
  }

  async function commitOffer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!configured || !commitToken || !activeBatch || !activeDemand || !riderId.trim()) { setNotice('Connect with a coordinator or donor token and select batch, demand, and rider UUID.'); return }
    const qty = Number(offerQuantity)
    const expectedRiderVersion = Number(riderVersion)
    if (!Number.isInteger(qty) || qty < 1) { setNotice('Offer quantity must be a positive whole number.'); return }
    if (qty > activeBatch.available_qty || qty > activeDemand.uncommitted_need) {
      setNotice(activeDemand.required_qty > 0 && activeDemand.received_qty >= activeDemand.required_qty
        ? 'This shelter has already received its requested quantity. Click Start fresh demo to run another delivery.'
        : `This batch has ${activeBatch.available_qty} available units and this shelter has ${activeDemand.uncommitted_need} uncommitted need. Choose a quantity within both limits, or click Resume existing dispatch to continue an accepted delivery.`)
      return
    }
    if (!Number.isInteger(expectedRiderVersion) || expectedRiderVersion < 0) { setNotice('Rider version must be a non-negative whole number.'); return }
    if (activeBatch.item_class !== activeDemand.item_class || !activeDemand.intake_open) { setNotice('Batch and demand must match, and the shelter intake must be open.'); return }
    const scenarioNow = scenarioTime ? new Date(scenarioTime) : new Date()
    if (!Number.isFinite(scenarioNow.valueOf())) { setNotice('The seeded scenario timestamp is invalid. Reload its seed output.'); return }
    const deliverBy = new Date(activeBatch.deliver_by)
    const expiresAt = new Date(Math.min(scenarioNow.getTime() + 5 * 60_000, deliverBy.getTime()))
    if (!Number.isFinite(expiresAt.valueOf()) || expiresAt <= scenarioNow) { setNotice('The batch has no valid offer window remaining. Refresh and check the scenario deadline.'); return }
    const payload = { batch_id: activeBatch.batch_id, demand_id: activeDemand.demand_id, rider_id: riderId.trim(), quantity: qty, expires_at: expiresAt.toISOString(), expected_batch_version: activeBatch.version, expected_demand_version: activeDemand.version, expected_rider_version: expectedRiderVersion }
    setCreating(true); setNotice('')
    try {
      const offer = await apiRequest<OfferResponse>(`${base}/api/v1/dispatch/commit`, commitToken, idempotentInit('commit', payload))
      commandKeys.current.delete('commit')
      setWorkflow({ lotId: offer.lot_id, offerId: offer.offer_id, riderId: offer.rider_id, demandId: offer.demand_id, qty: offer.qty, step: 'OFFERED', expectedLotVersion: offer.lot_version, pickedQty: 0, acceptedQty: 0, rejectedQty: 0, carriedQty: 0 })
      setPickedQuantity(String(offer.qty)); setAcceptedQuantity(String(offer.qty)); setRejectedQuantity('0')
      await refresh()
      setNotice(`Offer ${offer.offer_id.slice(0, 8)} published. It is now ready for the seeded rider to accept.`)
    } catch (error) { setNotice(`Offer was not published: ${errorMessage(error)}`) }
    finally { setCreating(false) }
  }

  function idempotentInit(scope: string, payload: object): RequestInit {
    const signature = JSON.stringify(payload)
    const previous = commandKeys.current.get(scope)
    const entry = previous?.signature === signature ? previous : { signature, key: randomKey() }
    commandKeys.current.set(scope, entry)
    return { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': entry.key }, body: JSON.stringify(payload) }
  }

  async function acceptOffer() {
    if (!workflow || !configured || !connection.tokens.rider.trim()) { setNotice('Connect with a rider bearer token to accept this offer.'); return }
    const payload = { rider_id: workflow.riderId, expected_version: workflow.expectedLotVersion }
    setCreating(true); setNotice('')
    try {
      const accepted = await apiRequest<AcceptResponse>(`${base}/api/v1/lots/${workflow.lotId}/accept`, connection.tokens.rider.trim(), idempotentInit('accept', payload))
      commandKeys.current.delete('accept')
      setRiderVersion((current) => String(Number(current) + 1))
      setWorkflow((current) => current ? { ...current, step: 'LEASED', expectedLotVersion: accepted.version, leaseExpiresAt: accepted.lease_expires_at } : current)
      await refresh()
      setNotice(`Lease accepted. Pickup lease expires ${formatDate(accepted.lease_expires_at)}.`)
    } catch (error) { setNotice(`Offer was not accepted: ${errorMessage(error)}`) }
    finally { setCreating(false) }
  }

  async function confirmPickup(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!workflow || workflow.step !== 'LEASED' || !configured || !connection.tokens.rider.trim()) { setNotice('Accept the offer with the connected rider token before pickup.'); return }
    const qty = Number(pickedQuantity)
    if (!Number.isInteger(qty) || qty < 1 || qty > workflow.qty) { setNotice('Pickup quantity must be positive and cannot exceed the accepted reservation.'); return }
    const payload = { picked_qty: qty, expected_version: workflow.expectedLotVersion }
    setCreating(true); setNotice('')
    try {
      const result = await apiRequest<CustodyResponse>(`${base}/api/v1/lots/${workflow.lotId}/pickup`, connection.tokens.rider.trim(), idempotentInit('pickup', payload))
      commandKeys.current.delete('pickup')
      setRiderVersion((current) => String(Number(current) + 1))
      setWorkflow((current) => current ? { ...current, step: 'IN_TRANSIT', expectedLotVersion: result.lot_version, pickedQty: result.picked_qty, acceptedQty: result.accepted_qty, rejectedQty: result.rejected_qty, carriedQty: result.carried_qty } : current)
      setAcceptedQuantity(String(result.carried_qty)); setRejectedQuantity('0')
      await refresh()
      setNotice(`Pickup confirmed by the API. ${result.carried_qty} ${activeBatch?.unit.toLowerCase() ?? 'units'} are in rider custody.`)
    } catch (error) { setNotice(`Pickup was not confirmed: ${errorMessage(error)}`) }
    finally { setCreating(false) }
  }

  async function confirmDelivery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!workflow || workflow.step !== 'IN_TRANSIT' || !configured || !connection.tokens.rider.trim()) { setNotice('An in-transit lot and connected rider bearer token are required.'); return }
    const accepted = Number(acceptedQuantity); const rejected = Number(rejectedQuantity)
    if (!Number.isInteger(accepted) || !Number.isInteger(rejected) || accepted < 0 || rejected < 0 || accepted + rejected < 1 || accepted + rejected > workflow.carriedQty) {
      setNotice('Accepted and rejected quantities must be whole numbers and cannot exceed the cargo still in custody.'); return
    }
    const payload = { accepted_qty: accepted, rejected_qty: rejected, expected_version: workflow.expectedLotVersion }
    setCreating(true); setNotice('')
    try {
      const result = await apiRequest<CustodyResponse>(`${base}/api/v1/lots/${workflow.lotId}/deliver`, connection.tokens.rider.trim(), idempotentInit('deliver', payload))
      commandKeys.current.delete('deliver')
      setRiderVersion((current) => String(Number(current) + 1))
      setWorkflow((current) => current ? { ...current, step: result.lot_state === 'DELIVERED' ? 'DELIVERED' : 'IN_TRANSIT', expectedLotVersion: result.lot_version, acceptedQty: result.accepted_qty, rejectedQty: result.rejected_qty, carriedQty: result.carried_qty } : current)
      setAcceptedQuantity(String(result.carried_qty)); setRejectedQuantity('0')
      await refresh()
      setNotice(`Delivery response recorded. ${result.accepted_qty} accepted, ${result.rejected_qty} rejected; ${result.carried_qty} remain in custody.`)
    } catch (error) { setNotice(`Delivery was not recorded: ${errorMessage(error)}`) }
    finally { setCreating(false) }
  }

  const navigation = [
    { id: 'overview', label: 'Control tower', description: 'Network overview', icon: <Activity size={17} /> },
    { id: 'donations', label: 'Donor workspace', description: 'Record donations', icon: <PackageCheck size={17} /> },
    { id: 'demand', label: 'Shelter workspace', description: 'Review shelter demand', icon: <ShieldCheck size={17} /> },
    { id: 'dispatch', label: 'Rider workflow', description: 'Accept, pick up, and deliver', icon: <Truck size={17} /> },
    { id: 'rider-map', label: 'Route map', description: 'View the rider route', icon: <Truck size={17} /> },
    { id: 'events', label: 'Event timeline', description: 'View persisted events', icon: <Clock3 size={17} /> },
  ]
  const scenarioClock = scenarioTime ? new Date(scenarioTime) : null
  const hasScenarioClock = scenarioClock !== null && Number.isFinite(scenarioClock.valueOf())

  return <div className="shell">
    <a className="skip-link" href="#main-content">Skip to main content</a>
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark"><Activity size={20} /></span><span>PeriShift</span></div>
      <div className="workspace-label">OPERATIONS</div>
      <nav aria-label="Main navigation">
        {navigation.map((item) => <a
          className={`nav-item ${activeSection === item.id ? 'active' : ''}`}
          href={`#${item.id}`}
          aria-label={item.description}
          aria-current={activeSection === item.id ? 'location' : undefined}
          title={item.description}
          key={item.id}
          onClick={() => setActiveSection(item.id)}
        >{item.icon}<span>{item.label}</span></a>)}
      </nav>
      <div className="sidebar-bottom"><span className={`live-dot ${streamState.includes('connected') ? '' : 'offline'}`} />{streamState.includes('connected') ? 'Live event stream' : 'Stream offline'}<span className="version">MVP</span></div>
    </aside>

    <main className="main-content" id="main-content" tabIndex={-1}>
      <header className="topbar" id="overview"><div className="crumb">Operations <span>/</span> Control tower</div><div className="topbar-status">
        <span className={`demo-mode-pill ${hasScenarioClock ? 'simulated' : ''}`}><i />{hasScenarioClock ? 'SIMULATED DEMO' : 'API WORKSPACE'}</span>
        {hasScenarioClock && <time className="scenario-clock" dateTime={scenarioClock!.toISOString()} title="Scenario time from the most recent connect, seed, or reset response."><Clock3 size={14} /><span>Scenario clock</span><strong>{formatDate(scenarioTime)}</strong></time>}
        <span className={`mode-pill ${lastRefresh && !batchError ? 'connected' : 'disconnected'}`}><i />{connectingDemo ? 'CONNECTING' : !configured ? 'NOT CONNECTED' : loading ? 'CHECKING API' : batchError ? 'API ERROR' : lastRefresh ? 'LIVE API' : 'TOKEN SET'}</span>
      </div></header>
      <div className="page" id="page-content">
        <div className="page-heading"><div><div className="eyebrow">PERISHABLE AID NETWORK</div><h1>Control tower</h1><p className="subtitle">Create a batch, publish an offer, accept the lease, and confirm physical custody.</p></div><button className="primary-button" onClick={() => void refresh()} disabled={!configured || loading} type="button"><RefreshCw size={15} className={loading ? 'spin' : ''} /> Refresh live data</button></div>

        <section className="demo-role-banner" aria-label="Demo role guide">
          <div><span className="eyebrow">DEMO ROLE GUIDE</span><strong>API-backed actions by role</strong></div>
          <span className="role-chip">Coordinator · publishes offers</span>
          <span className="role-chip">Donor · records donations</span>
          <span className="role-chip">Rider · confirms custody</span>
          <span className="role-chip">Shelter · reports demand</span>
        </section>

        <section className="connection panel" aria-labelledby="connection-title">
          <div className="panel-heading"><div><h2 id="connection-title"><KeyRound size={15} /> Demo workspace</h2><p>Your local demo connects automatically. Start a fresh demo whenever you want to repeat the delivery.</p></div><span className="connection-state">{connectingDemo ? <><LoaderCircle size={14} className="spin" /> Connecting</> : lastRefresh && !batchError ? <><Wifi size={14} /> Live API data</> : configured ? <><Wifi size={14} /> Credentials set</> : <><WifiOff size={14} /> Disconnected</>}</span></div>
          <div className="connection-actions"><button className="secondary-button" type="button" disabled={connectingDemo || creating || loading} onClick={() => void connectLocalDemo()}><RefreshCw size={15} /> Reconnect demo</button><button className="primary-button" type="button" disabled={!configured || !connection.tokens.coordinator.trim() || !connection.scenarioId || connectingDemo || creating || loading} onClick={() => void startFreshDemo()}>Start fresh demo</button></div>
          <details className="connection-settings"><summary>Manual API and scenario settings</summary>
          <form className="seed-import" onSubmit={loadSeedResult}><label>Seed output JSON<textarea className="seed-json" value={seedJson} onChange={(event) => setSeedJson(event.target.value)} rows={2} placeholder="Paste the seed command's JSON output here (includes local demo role tokens)." autoComplete="off" spellCheck={false} /></label><button className="secondary-button" type="submit">Load seeded scenario</button></form>
          <div className="connection-grid"><label>API base URL<input value={apiBase} onChange={(event) => setApiBase(event.target.value)} placeholder="Same-origin proxy (local API default)" autoComplete="url" /></label><label>Scenario UUID<input value={scenarioId} onChange={(event) => setScenarioId(event.target.value)} placeholder="Scenario ID" /></label><label>Donor resource UUID<input value={donorId} onChange={(event) => setDonorId(event.target.value)} placeholder="Donor ID" /></label></div>
          <div className="token-grid">{(['coordinator', 'donor', 'rider', 'shelter'] as Role[]).map((role) => <label key={role}>{role[0].toUpperCase() + role.slice(1)} bearer token<input type="password" autoComplete="off" value={tokens[role]} onChange={(event) => updateToken(role, event.target.value)} placeholder={`${role} JWT`} /></label>)}</div>
          <div className="connection-foot"><span><CircleHelp size={13} /> Coordinator publishes offers; rider accepts/picks up/delivers; shelter token reads demand.</span><span>{lastRefresh ? `Last updated ${lastRefresh.toLocaleTimeString()}` : 'No live data loaded'}</span></div>
          <div className="connection-actions"><button className="secondary-button" type="button" onClick={connectWithDraft}>Apply credentials &amp; connect</button>{draftChanged && <span className="form-hint">Edited settings are not active until applied.</span>}</div>
          </details>
          {demandError && <div className="inline-warning"><AlertCircle size={14} /> Demand unavailable: {demandError}</div>}
        </section>

        {notice && <div className={`notice ${notice.includes('recorded by the API') || notice.includes('confirmed by the API') ? 'success' : ''}`} role="status"><AlertCircle size={17} />{notice}</div>}

        <div className="section-title"><h2>Network snapshot</h2><span>{loading ? 'Loading from API…' : lastRefresh ? 'Source: live API records' : 'Connect to load records'}</span></div>
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
              <div className="form-row"><label>Item class<select value={itemClass} onChange={(event) => setItemClass(event.target.value as ItemClass)}>{Object.entries(itemNames).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label>Quantity ({unitByItem[itemClass].toLowerCase()})<input type="number" min="1" step="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} placeholder="0" /></label></div>
              <div className="form-row"><label>Ready at<input type="datetime-local" value={readyAt} onChange={(event) => setReadyAt(event.target.value)} /></label><label>Declared safe by<input type="datetime-local" value={safeBy} onChange={(event) => setSafeBy(event.target.value)} /></label></div>
              <label>Serving buffer <span className="optional">seconds after pickup</span><input type="number" min="0" step="1" value={bufferSeconds} onChange={(event) => setBufferSeconds(event.target.value)} /></label>
              <button className="primary-button submit-button" type="submit" disabled={!configured || !connection.tokens.donor.trim() || creating}>{creating ? <LoaderCircle size={15} className="spin" /> : <Plus size={15} />}{creating ? 'Saving…' : 'Record donation'}</button>
            </form>
          </section>
          <section className="panel" id="demand" aria-busy={loading}>
            <div className="panel-heading"><div><h2>Shelter demand</h2><p>Demand visible to the authenticated shelter actor.</p></div><span className="section-icon"><ShieldCheck size={16} /></span></div>
            {demandError ? <ApiError message={demandError} /> : loading && !lastRefresh ? <LoadingState label="Loading shelter demand from the API…" /> : demands.length ? <div className="table-wrap"><table><thead><tr><th>Item</th><th>Need</th><th>State</th></tr></thead><tbody>{demands.map((demand) => <tr key={demand.demand_id}><td><strong>{itemNames[demand.item_class]}</strong><small>{demand.demand_id.slice(0, 8)} · {demand.unit.toLowerCase()}</small></td><td>{demand.uncommitted_need} / {demand.required_qty}</td><td><span className={`status ${demand.intake_open ? 'open' : 'closed'}`}>{demand.intake_open ? 'Open' : 'Closed'}</span></td></tr>)}</tbody></table></div> : <EmptyState loaded={lastRefresh !== null} message="No demand records returned for this actor." />}
          </section>
        </div>

        <section className="panel batches-panel" aria-busy={loading}>
          <div className="panel-heading"><div><h2>Donation batches</h2><p>Inventory buckets are read directly from the batch ledger.</p></div><span className="record-count">{batches.length} records</span></div>
          {batchError ? <ApiError message={batchError} /> : loading && !lastRefresh ? <LoadingState label="Loading donation batches from the API…" /> : batches.length ? <div className="table-wrap"><table><thead><tr><th>Item / batch</th><th>Available</th><th>Reserved</th><th>In custody</th><th>Delivered</th><th>Safe by</th></tr></thead><tbody>{batches.map((batch) => <tr key={batch.batch_id}><td><strong>{itemNames[batch.item_class]}</strong><small>{batch.batch_id.slice(0, 8)} · {batch.total_qty} {batch.unit.toLowerCase()}</small></td><td>{batch.available_qty}</td><td>{batch.reserved_qty}</td><td>{batch.carried_qty}</td><td>{batch.delivered_qty}</td><td>{formatDate(batch.safe_by)}</td></tr>)}</tbody></table></div> : <EmptyState loaded={lastRefresh !== null} message="No batches found in this scope." />}
        </section>

        <section className="panel workflow-panel" id="dispatch">
          <div className="panel-heading"><div><h2>End-to-end dispatch</h2><p>Every transition uses the backend's version checks, leases, and custody rules.</p></div><span className={`workflow-state ${workflow?.step === 'DELIVERED' ? 'done' : workflow ? 'active' : ''}`}><i />{workflow?.step ?? 'NOT STARTED'}</span></div>
          {activeDemand && activeDemand.required_qty > 0 && activeDemand.received_qty >= activeDemand.required_qty && <div className="notice success"><CheckCircle2 size={17} /><span>This shelter's request is fulfilled. Start a fresh demo to repeat the delivery.</span><button className="secondary-button" type="button" disabled={!configured || !connection.tokens.coordinator.trim() || creating || loading} onClick={() => void startFreshDemo()}>Start fresh demo</button></div>}
          {(!workflow || workflow.step === 'DELIVERED') && <button className="secondary-button" type="button" disabled={!configured || !riderId.trim() || creating} onClick={() => void resumeDispatch()}>Resume existing dispatch</button>}
          {(!workflow || workflow.step === 'DELIVERED') && <form className="workflow-form" onSubmit={(event) => void commitOffer(event)}>
            <label>Batch<select value={selectedBatchId} onChange={(event) => setSelectedBatchId(event.target.value)}><option value="">Choose a live batch</option>{batches.map((batch) => <option key={batch.batch_id} value={batch.batch_id}>{itemNames[batch.item_class]} · {batch.available_qty} {batch.unit.toLowerCase()} available · v{batch.version}</option>)}</select></label>
            <label>Demand<select value={selectedDemandId} onChange={(event) => setSelectedDemandId(event.target.value)}><option value="">Choose a live demand</option>{demands.map((demand) => <option key={demand.demand_id} value={demand.demand_id}>{itemNames[demand.item_class]} · {demand.uncommitted_need} needed · v{demand.version}</option>)}</select></label>
            <label>Seeded rider UUID<input value={riderId} onChange={(event) => setRiderId(event.target.value)} placeholder="Rider resource ID" /></label>
            <label>Rider version<input type="number" min="0" step="1" value={riderVersion} onChange={(event) => setRiderVersion(event.target.value)} /></label>
            <label>Offer quantity<input type="number" min="1" step="1" value={offerQuantity} onChange={(event) => setOfferQuantity(event.target.value)} placeholder={activeDemand ? String(Math.min(activeBatch?.available_qty ?? 0, activeDemand.uncommitted_need)) : '0'} /></label>
            <button className="primary-button" type="submit" disabled={!configured || !commitToken || !activeBatch || !activeDemand || !riderId.trim() || creating}><Play size={15} /> Publish offer</button>
            {!commitToken && <p className="form-hint">Apply a coordinator or donor token to publish the offer.</p>}
          </form>}
          {workflow && <div className="active-workflow">
            <div className="workflow-summary"><div><span>LOT</span><strong>{workflow.lotId}</strong></div><div><span>RIDER</span><strong>{workflow.riderId}</strong></div><div><span>OFFER QUANTITY</span><strong>{workflow.qty} {activeBatch?.unit.toLowerCase() ?? 'units'}</strong></div><div><span>LOT VERSION</span><strong>{workflow.expectedLotVersion}</strong></div>{workflow.leaseExpiresAt && <div><span>LEASE EXPIRES</span><strong>{formatDate(workflow.leaseExpiresAt)}</strong></div>}</div>
            <div className="workflow-actions">
              {workflow.step === 'OFFERED' && <button className="primary-button" type="button" disabled={!configured || !connection.tokens.rider.trim() || creating} onClick={() => void acceptOffer()}><CheckCircle2 size={15} /> Accept offer as rider</button>}
              {workflow.step === 'LEASED' && <form className="inline-action" onSubmit={(event) => void confirmPickup(event)}><label>Picked quantity<input type="number" min="1" max={workflow.qty} step="1" value={pickedQuantity} onChange={(event) => setPickedQuantity(event.target.value)} /></label><button className="primary-button" type="submit" disabled={!configured || !connection.tokens.rider.trim() || creating}><PackageCheck size={15} /> Confirm pickup</button></form>}
              {workflow.step === 'IN_TRANSIT' && <form className="inline-action" onSubmit={(event) => void confirmDelivery(event)}><label>Accept at shelter<input type="number" min="0" max={workflow.carriedQty} step="1" value={acceptedQuantity} onChange={(event) => setAcceptedQuantity(event.target.value)} /></label><label>Reject / unsafe<input type="number" min="0" max={workflow.carriedQty} step="1" value={rejectedQuantity} onChange={(event) => setRejectedQuantity(event.target.value)} /></label><span className="cargo-remaining">Still carried: {workflow.carriedQty}</span><button className="primary-button" type="submit" disabled={!configured || !connection.tokens.rider.trim() || creating}><CheckCircle2 size={15} /> Confirm delivery</button></form>}
              {workflow.step === 'DELIVERED' && <div className="completed-state"><CheckCircle2 size={17} /> Lot closed by the API. Start another offer from current inventory.</div>}
              {(!configured || !connection.tokens.rider.trim()) && workflow.step !== 'DELIVERED' && <p className="form-hint">Apply a rider bearer token to perform this transition.</p>}
              <button className="text-button" type="button" onClick={() => { setWorkflow(null); setNotice('Workflow cleared from this page. The backend records remain unchanged.') }}>Clear local workflow</button>
            </div>
          </div>}
          <div className="workflow-steps">{['Offer published', 'Lease accepted', 'Pickup confirmed', 'Delivery confirmed'].map((step, index) => <div className={`workflow-step ${workflow && ['OFFERED', 'LEASED', 'IN_TRANSIT', 'DELIVERED'][index] === workflow.step ? 'current' : ''} ${workflow && ['OFFERED', 'LEASED', 'IN_TRANSIT', 'DELIVERED'].indexOf(workflow.step) > index ? 'complete' : ''}`} key={step}><span>{String(index + 1).padStart(2, '0')}</span><strong>{step}</strong>{index < 3 && <ArrowRight size={14} />}</div>)}</div>
        </section>

        <DemoRouteMap baseUrl={base} token={configured ? (connection.tokens.coordinator || connection.tokens.rider).trim() : ''} riderId={riderId} batchId={selectedBatchId} demandId={selectedDemandId} workflow={workflow} />

        <section className="panel events-panel" id="events">
          <div className="panel-heading"><div><h2>Live event stream</h2><p>Persisted scenario events from the authenticated SSE endpoint.</p></div><span className={`workflow-state ${streamState.includes('connected') ? 'active' : ''}`}><i />{streamState}</span></div>
          {events.length ? <div className="event-list">{events.map((event) => <div className="event-row" key={event.id}><span className="event-id">#{event.id}</span><div className="event-copy"><strong>{event.type.replaceAll('_', ' ')}</strong><small>{event.aggregate_type} · {event.aggregate_id}</small></div><time>{formatDate(event.occurred_at)}</time></div>)}</div> : <EmptyState loaded={streamState.includes('connected')} message="No events received in this stream yet." />}
        </section>
        <footer>Operational state is sourced from authenticated API responses. Failed commands remain visible as errors and do not advance local workflow state.</footer>
      </div>
    </main>
  </div>
}

function ApiError({ message }: { message: string }) { return <div className="api-error" role="alert"><AlertCircle size={16} /><div><strong>Could not load this resource</strong><span>{message}</span></div></div> }
function EmptyState({ loaded, message }: { loaded: boolean; message: string }) { return <div className="empty-state" role="status"><PackageCheck size={21} /><strong>{loaded ? message : 'Connect to load live records'}</strong><span>{loaded ? 'No records have arrived yet.' : 'Add role credentials and refresh.'}</span></div> }
function LoadingState({ label }: { label: string }) { return <div className="loading-state" role="status" aria-live="polite"><strong>{label}</strong><span /><span /><span /></div> }

export default App
