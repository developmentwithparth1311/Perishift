import { useEffect, useRef, useState } from 'react'
import { AlertCircle, MapPin, Navigation, Truck } from 'lucide-react'
import * as L from 'leaflet'
import 'leaflet/dist/leaflet.css'

type Location = { name: string; latitude: number; longitude: number }
type Route = {
  lot_id: string | null; state: string | null; simulated: boolean
  pickup: Location; destination: Location; rider: Location & { location_known: boolean }
}
type Props = {
  baseUrl: string; token: string; riderId: string; batchId: string; demandId: string
  workflow: { lotId: string; step: string; riderId: string } | null
}

const TRAVEL_MS = 20_000
const point = (location: Location): L.LatLngTuple => [location.latitude, location.longitude]

function markerIcon(kind: 'pickup' | 'destination' | 'rider') {
  const symbol = kind === 'rider'
    ? '<path d="M3 7h11v10H3zM14 10h4l3 4v3h-7M7 17a2 2 0 1 0 0 .1M17 17a2 2 0 1 0 0 .1"/>'
    : kind === 'pickup' ? '<path d="m12 3 8 5v9l-8 4-8-4V8l8-5Zm0 9 8-4M12 12 4 8m8 4v9"/>'
      : '<path d="M4 21V8l8-5 8 5v13M9 21v-7h6v7M8 9h1m6 0h1"/>'
  return L.divIcon({
    className: `route-marker route-marker-${kind}`,
    html: `<span><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${symbol}</svg></span>`,
    iconSize: [38, 38], iconAnchor: [19, 19], tooltipAnchor: [0, -22],
  })
}

function label(text: string) {
  const element = document.createElement('span')
  element.textContent = text
  return element
}

export default function DemoRouteMap({ baseUrl, token, riderId, batchId, demandId, workflow }: Props) {
  const [route, setRoute] = useState<Route | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [tileError, setTileError] = useState(false)
  const [progress, setProgress] = useState(0)
  const container = useRef<HTMLDivElement>(null)
  const riderMarker = useRef<L.Marker | null>(null)
  const traveledLine = useRef<L.Polyline | null>(null)
  const lotId = workflow?.lotId ?? ''
  const step = workflow?.step ?? ''
  const mapRiderId = workflow?.riderId || riderId.trim()
  const ready = Boolean(token && mapRiderId && (lotId || (batchId && demandId)))

  useEffect(() => {
    setRoute(null)
    setError('')
    setProgress(0)
    if (!ready) { setLoading(false); return }
    const controller = new AbortController()
    const timeout = window.setTimeout(() => controller.abort(), 15_000)
    let disposed = false
    setLoading(true)
    const params = new URLSearchParams({ rider_id: mapRiderId })
    if (lotId) params.set('lot_id', lotId)
    else { params.set('batch_id', batchId); params.set('demand_id', demandId) }
    void (async () => {
      try {
        const response = await fetch(`${baseUrl}/api/v1/dispatch/map?${params}`, {
          headers: { Authorization: `Bearer ${token}` }, signal: controller.signal,
        })
        const body = await response.json()
        if (!response.ok) throw new Error(body.detail?.message || body.error?.message || `Map request failed (${response.status}).`)
        if (!controller.signal.aborted) setRoute(body as Route)
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Could not load route locations.')
        else if (!disposed) setError('Route request timed out. Reconnect the demo and try again.')
      } finally {
        window.clearTimeout(timeout)
        if (!disposed) setLoading(false)
      }
    })()
    return () => { disposed = true; controller.abort(); window.clearTimeout(timeout) }
  }, [baseUrl, token, mapRiderId, batchId, demandId, lotId, step, ready])

  const phase = route?.state ?? step
  const moving = Boolean(route?.simulated && phase === 'IN_TRANSIT' && route.lot_id)
  const delivered = phase === 'DELIVERED'

  useEffect(() => {
    if (delivered) { setProgress(1); return }
    if (!moving || !route?.lot_id) { setProgress(0); return }
    const storageKey = `perishift:demo-trip:${route.lot_id}`
    let startedAt = Date.now()
    try {
      const saved = Number(sessionStorage.getItem(storageKey))
      if (saved > 0 && Number.isFinite(saved) && saved <= startedAt) startedAt = saved
      else sessionStorage.setItem(storageKey, String(startedAt))
    } catch { /* Animation also works when browser storage is disabled. */ }
    const tick = () => setProgress(Math.min(1, Math.max(0, (Date.now() - startedAt) / TRAVEL_MS)))
    tick()
    const timer = window.setInterval(tick, 100)
    return () => window.clearInterval(timer)
  }, [moving, delivered, route?.lot_id])

  useEffect(() => {
    if (!container.current || !route) return
    setTileError(false)
    const map = L.map(container.current, { scrollWheelZoom: false }).setView(point(route.pickup), 15)
    const tiles = L.tileLayer(import.meta.env.VITE_MAP_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: import.meta.env.VITE_MAP_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      maxZoom: 19,
    }).addTo(map)
    tiles.on('tileerror', () => setTileError(true))
    const pickup = point(route.pickup)
    const destination = point(route.destination)
    L.polyline([pickup, destination], { color: '#69865a', weight: 5, opacity: 0.45, dashArray: '8 9' }).addTo(map)
    traveledLine.current = L.polyline([pickup, pickup], { color: '#314d30', weight: 5 }).addTo(map)
    L.marker(pickup, { icon: markerIcon('pickup') }).addTo(map).bindTooltip(label(`Pickup: ${route.pickup.name}`))
    L.marker(destination, { icon: markerIcon('destination') }).addTo(map).bindTooltip(label(`Destination: ${route.destination.name}`))
    riderMarker.current = L.marker(point(route.rider), { icon: markerIcon('rider'), zIndexOffset: 1000 })
      .addTo(map).bindTooltip(label(route.simulated ? `${route.rider.name} · simulated movement` : route.rider.name))
    const bounds = L.latLngBounds([pickup, destination, point(route.rider)])
    map.fitBounds(bounds, { padding: [55, 55], maxZoom: 16 })
    const observer = new ResizeObserver(() => map.invalidateSize())
    observer.observe(container.current)
    return () => {
      observer.disconnect()
      riderMarker.current = null
      traveledLine.current = null
      map.remove()
    }
  }, [route])

  useEffect(() => {
    if (!route || !riderMarker.current) return
    const pickup = point(route.pickup)
    const destination = point(route.destination)
    const position: L.LatLngTuple = delivered ? destination : moving
      ? [pickup[0] + (destination[0] - pickup[0]) * progress, pickup[1] + (destination[1] - pickup[1]) * progress]
      : phase === 'LEASED' ? pickup : point(route.rider)
    riderMarker.current.setLatLng(position)
    traveledLine.current?.setLatLngs([pickup, moving || delivered ? position : pickup])
  }, [route, phase, moving, delivered, progress])

  const status = delivered ? 'Delivery confirmed' : moving
    ? progress >= 1 ? 'At destination · confirm delivery above' : 'Rider heading to shelter'
    : phase === 'LEASED' ? 'At pickup · confirm pickup to begin' : 'Route preview · waiting for pickup'

  return <section className="panel route-panel" id="rider-map" aria-labelledby="rider-map-title">
    <div className="panel-heading"><div><h2 id="rider-map-title"><Navigation size={16} /> Rider journey</h2><p>Pickup to destination, connected to your dispatch workflow.</p></div><span className="route-demo-badge">{route && !route.simulated ? 'LOCATION PREVIEW' : 'SIMULATED DEMO MOVEMENT'}</span></div>
    {error ? <div className="api-error" role="alert"><AlertCircle size={16} /><div><strong>Could not load the rider map</strong><span>{error}</span></div></div>
      : !route ? <div className="empty-state"><MapPin size={22} /><strong>{loading ? 'Loading route locations…' : 'Connect the demo and choose a batch, demand, and rider.'}</strong></div>
        : <>
          <div className="route-map" ref={container} role="region" aria-label={`Delivery map from ${route.pickup.name} to ${route.destination.name}`} />
          {tileError && <p className="route-tile-warning">Map tiles are unavailable. Pickup, destination, and the route preview remain visible.</p>}
          <div className="route-detail-row"><div><span className="route-dot pickup" /><div><small>PICKUP</small><strong>{route.pickup.name}</strong></div></div><div><span className="route-dot destination" /><div><small>DESTINATION</small><strong>{route.destination.name}</strong></div></div><div className="route-rider-name"><Truck size={17} /><div><small>RIDER</small><strong>{route.rider.name}</strong></div></div></div>
          <div className="route-progress-heading"><span role="status">{status}</span><strong>{moving || delivered ? `${Math.round(progress * 100)}%` : 'Ready'}</strong></div>
          <div className="route-progress" role="progressbar" aria-label="Simulated rider journey" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${progress * 100}%` }} /></div>
          <p className="route-caption">{route.simulated ? 'The rider travels in 20 seconds after pickup confirmation. This is a straight-line demo path, not live GPS or road navigation. Confirm delivery above to record the handoff.' : 'The line connects recorded locations and is not road navigation. No simulated movement is applied to live riders.'}{!route.rider.location_known && ' Rider location is unavailable; the pickup is used as its starting point.'}</p>
        </>}
  </section>
}
