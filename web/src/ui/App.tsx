import { Activity, ArrowUpRight, Clock3, PackageCheck, ShieldCheck, Truck } from 'lucide-react'

const events = [
  { time: '14:32', title: 'Dispatch network ready', detail: 'Connect the API to begin a live scenario.', tone: 'teal' },
  { time: '—', title: 'No active commitments', detail: 'Accepted leases and custody events will appear here.', tone: 'muted' },
  { time: '—', title: 'Recovery standing by', detail: 'Destination changes will be evaluated against live demand.', tone: 'muted' },
]

function App() {
  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand-mark"><Activity size={20} /></span><span>PeriShift</span></div>
        <div className="workspace-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          <a className="nav-item active" href="#overview"><Activity size={17} /> Control tower</a>
          <a className="nav-item" href="#donations"><PackageCheck size={17} /> Donations <span className="nav-count">0</span></a>
          <a className="nav-item" href="#riders"><Truck size={17} /> Rider network</a>
          <a className="nav-item" href="#safety"><ShieldCheck size={17} /> Safety rules</a>
        </nav>
        <div className="sidebar-bottom"><span className="live-dot" /> API scaffold ready <span className="version">v0.1</span></div>
      </aside>

      <section className="main-content" id="overview">
        <header className="topbar"><div className="crumb">Operations <span>/</span> Control tower</div><div className="mode-pill"><span className="mode-dot" /> DEMO WORKSPACE</div></header>
        <div className="page">
          <div className="page-heading">
            <div><div className="eyebrow">PERISHABLE AID NETWORK</div><h1>Control tower</h1><p className="subtitle">A clear view of supply, commitments, and the time left to act.</p></div>
            <button className="primary-button" type="button" disabled>Start a scenario <ArrowUpRight size={16} /></button>
          </div>

          <div className="notice"><span className="notice-icon"><Clock3 size={18} /></span><div><strong>Waiting for the first scenario</strong><p>The project shell is ready. Batch intake, dispatch, and simulated operations are the next implementation steps.</p></div></div>

          <div className="section-title"><h2>Network snapshot</h2><span>Updates will appear when API services are connected</span></div>
          <div className="metrics">
            <article className="metric-card"><div className="metric-top"><span>Safe delivered</span><PackageCheck size={17} /></div><div className="metric-value">0 <small>units</small></div><div className="metric-foot">Confirmed receipts only</div></article>
            <article className="metric-card"><div className="metric-top">In transit <Truck size={17} /></div><div className="metric-value">0 <small>units</small></div><div className="metric-foot">Physical custody tracked</div></article>
            <article className="metric-card"><div className="metric-top">At risk <span className="risk-tag">WATCH</span></div><div className="metric-value">0 <small>units</small></div><div className="metric-foot">Escalations need attention</div></article>
            <article className="metric-card"><div className="metric-top">Open donations <PackageCheck size={17} /></div><div className="metric-value">0 <small>batches</small></div><div className="metric-foot">Available for planning</div></article>
          </div>

          <div className="content-grid">
            <section className="panel network-panel"><div className="panel-heading"><div><h2>Live network</h2><p>Riders and receiving locations</p></div><span className="state-label"><i /> IDLE</span></div><div className="map-placeholder"><div className="map-grid" /><div className="map-center"><span className="map-ring"><Activity size={19} /></span><strong>Network is clear</strong><small>Start a seeded scenario to see live activity</small></div><span className="map-caption">SIMULATED MAP · NO LIVE GPS</span></div></section>
            <section className="panel event-panel"><div className="panel-heading"><div><h2>Event timeline</h2><p>Recent operational decisions</p></div><button className="text-button" type="button" disabled>View all <ArrowUpRight size={14} /></button></div><div className="event-list">{events.map((event) => <div className="event-row" key={event.title}><div className={`event-marker ${event.tone}`} /><div className="event-copy"><div className="event-title">{event.title}</div><div className="event-detail">{event.detail}</div></div><time>{event.time}</time></div>)}</div></section>
          </div>
          <footer>Safety, demand, and custody checks belong to the API. This screen is an initial scaffold; it does not simulate successful delivery.</footer>
        </div>
      </section>
    </main>
  )
}

export default App
