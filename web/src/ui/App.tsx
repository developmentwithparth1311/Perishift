import { useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  HeartHandshake,
  MapPin,
  Menu,
  PackageCheck,
  ShieldCheck,
  Truck,
  X,
} from 'lucide-react'

type PlateKind = 'list' | 'phone' | 'card' | 'activity' | 'route' | 'detail' | 'handoff' | 'shelter'
type Palette = 'blue' | 'green' | 'rose' | 'copper' | 'teal' | 'violet' | 'olive' | 'sand' | 'sky' | 'grey'

const plates: Array<{ place: string; palette: Palette; kind: PlateKind; className: string; label: string }> = [
  { place: 'p1', palette: 'grey', kind: 'detail', className: 'invoice', label: 'Pickup detail card' },
  { place: 'p2', palette: 'green', kind: 'list', className: 'people', label: 'Rider capacity list' },
  { place: 'p3', palette: 'blue', kind: 'phone', className: 'phone', label: 'Donation overview on a phone' },
  { place: 'p4', palette: 'rose', kind: 'card', className: 'commitment', label: 'Delivery commitment card' },
  { place: 'p5', palette: 'copper', kind: 'activity', className: 'activity', label: 'Recent dispatch activity' },
  { place: 'p6', palette: 'teal', kind: 'detail', className: 'safe-window', label: 'Delivery window details' },
  { place: 'p7', palette: 'olive', kind: 'list', className: 'shelter-list', label: 'Receiving capacity list' },
  { place: 'p8', palette: 'violet', kind: 'route', className: 'route', label: 'Route and destination card' },
  { place: 'p9', palette: 'sand', kind: 'handoff', className: 'pickup-check', label: 'Pickup confirmation with 40 portions checked' },
]

const workRows = [
  {
    number: 'Ø1', title: 'Make surplus visible',
    description: 'A clear starting point for donors to share what is ready, how much is available, and how long it can safely travel.',
    links: ['Donation intake', 'Readiness window', 'Quantity ledger'], palette: 'blue' as Palette,
  },
  {
    number: 'Ø2', title: 'Match the real constraints',
    description: 'Proposed handoffs account for rider capacity, recipient demand, handling needs, and the time remaining.',
    links: ['Deadline checks', 'Capacity matching', 'Receiving fit', 'Estimated routes'], palette: 'green' as Palette,
  },
  {
    number: 'Ø3', title: 'Keep custody clear',
    description: 'Follow the food from reservation to pickup to receipt, with a readable record of what changed along the way.',
    links: ['Offer and accept', 'Pickup confirmation', 'Receipt record', 'Audit timeline'], palette: 'rose' as Palette,
  },
  {
    number: 'Ø4', title: 'Re-plan when life happens',
    description: 'When a rider goes quiet or a destination changes, review the next safe option without losing track of the food.',
    links: ['Progress review', 'Demand changes', 'Alternate receiver'], palette: 'sand' as Palette,
  },
]

const roles = [
  { number: 'Ø1', title: 'Donors', copy: 'Share ready-to-move surplus with the details a safe handoff needs.', icon: PackageCheck, tone: 'sage' },
  { number: 'Ø2', title: 'Volunteer riders', copy: 'See a clear commitment, route estimate, and next action.', icon: Truck, tone: 'blue' },
  { number: 'Ø3', title: 'Receiving partners', copy: 'Keep demand and intake capacity visible as plans change.', icon: HeartHandshake, tone: 'rose' },
  { number: 'Ø4', title: 'Coordinators', copy: 'Understand what is moving, what is at risk, and why.', icon: ShieldCheck, tone: 'sand' },
]

const quickstart = [
  { label: 'DONORS', title: 'Offer a donation', number: 'Ø1', type: 'donation' },
  { label: 'PLANNING', title: 'Review a match', number: 'Ø2', type: 'match' },
  { label: 'RIDERS', title: 'Follow a handoff', number: 'Ø3', type: 'rider' },
  { label: 'RECEIVERS', title: 'Confirm a receipt', number: 'Ø4', type: 'receipt' },
]

function Brand({ markOnly = false }: { markOnly?: boolean }) {
  return <a className={`brand ${markOnly ? 'brand-mark-only' : ''}`} href="#top" aria-label="PeriShift home">
    <span className="brand-glyph" aria-hidden="true"><i /><i /><i /><i /></span>
    <span className="brand-word">perishift</span>
  </a>
}

function GlassUI({ kind }: { kind: PlateKind }) {
  if (kind === 'list') return <div className="glass-ui ui-list"><div className="glass-eyebrow">AVAILABLE NETWORK</div><div className="glass-list-row"><i className="avatar a1" /><span>Meera · rider</span><b>24 meals</b></div><div className="glass-list-row"><i className="avatar a2" /><span>Sam · rider</span><b>18 meals</b></div><div className="glass-list-row"><i className="avatar a3" /><span>North Kitchen</span><b>32 needed</b></div><div className="glass-list-row"><i className="avatar a4" /><span>Good Table</span><b>40 needed</b></div></div>
  if (kind === 'phone') return <div className="phone-frame"><div className="phone-speaker" /><div className="phone-status">9:41 <span>●●●</span></div><div className="phone-header">Donation <span>•••</span></div><div className="phone-tag">READY TO PLAN</div><div className="phone-meal">Fresh meals</div><div className="phone-qty">40 <small>portions</small></div><div className="phone-buttons"><b>View pickup</b><b>Details</b></div><div className="phone-rule" /><div className="phone-micro">Safe by&nbsp; 15:45<br />Ready at&nbsp; 14:10</div></div>
  if (kind === 'card') return <div className="glass-ui ui-commit"><div className="glass-eyebrow">ACTIVE COMMITMENT <span>•••</span></div><strong>Fresh meals</strong><div className="commit-number"># PS–0428</div><div className="commit-card-line" /><div className="commit-foot"><span>40 portions</span><span>1 rider</span></div><div className="commit-stamp"><Check size={13} /></div></div>
  if (kind === 'activity') return <div className="glass-ui ui-activity"><div className="glass-eyebrow">EVENT TIMELINE</div><div className="activity-row"><i /> <span>Offer accepted</span><time>14:18</time></div><div className="activity-row"><i /> <span>Pickup confirmed</span><time>14:27</time></div><div className="activity-row dim"><i /> <span>Receipt pending</span><time>—</time></div></div>
  if (kind === 'route') return <div className="glass-ui ui-route"><div className="glass-eyebrow">ESTIMATED ROUTE</div><div className="route-stat"><MapPin size={12} /><span>North Kitchen</span></div><div className="route-dash"><i /></div><div className="route-stat"><HeartHandshake size={12} /><span>Good Table</span></div><div className="route-pill"><Clock3 size={10} /> 18 min · simulated</div></div>
  if (kind === 'handoff') return <div className="glass-ui ui-handoff"><div className="glass-eyebrow">PICKUP CHECK</div><strong>40 portions</strong><div className="handoff-check"><Check size={13} /><span>Quantity confirmed</span></div><div className="mini-button">Confirm pickup</div></div>
  if (kind === 'shelter') return <div className="glass-ui ui-shelter"><div className="glass-eyebrow">RECEIPT STATUS</div><div className="receipt-mark"><Check size={15} /></div><strong>Receipt recorded</strong><span>30 portions accepted</span><div className="mini-button">View ledger</div></div>
  return <div className="glass-ui ui-detail"><div className="glass-eyebrow">{kind === 'detail' ? 'DONATION DETAILS' : 'RECEIVING PARTNER'}</div><div className="detail-name">{kind === 'detail' ? 'Fresh meals' : 'Good Table'}</div><div className="detail-row"><span>{kind === 'detail' ? 'Pickup window' : 'Intake need'}</span><b>{kind === 'detail' ? '14:10 – 14:25' : '30 portions'}</b></div><div className="detail-row"><span>Safe until</span><b>15:45</b></div><div className="detail-progress"><i /></div><div className="mini-button">{kind === 'detail' ? 'View donation' : 'Check fit'}</div></div>
}

function FloatingPlate({ plate }: { plate: (typeof plates)[number] }) {
  return <div className={`hero-plate ${plate.place} palette-${plate.palette}`} aria-label={plate.label}>
    <div className="plate-texture" />
    <GlassUI kind={plate.kind} />
  </div>
}

function ProductPreview({ palette, index }: { palette: Palette; index: number }) {
  const previews: PlateKind[] = ['phone', 'list', 'card', 'detail']
  return <div className={`row-preview palette-${palette}`} aria-hidden="true">
    <div className="preview-texture" />
    <GlassUI kind={previews[index % previews.length]} />
    <span className="preview-chip">PERISHIFT · SAMPLE UI</span>
  </div>
}

function WorkRow({ row, index }: { row: (typeof workRows)[number]; index: number }) {
  const [active, setActive] = useState(0)
  return <article className="work-row">
    <div className="row-index">{row.number}</div>
    <div className="row-description"><h3>{row.title}</h3><p>{row.description}</p><span className="row-hover-note">{row.links[active]}</span></div>
    <div className="row-art"><ProductPreview palette={row.palette} index={active + index} /></div>
    <div className="row-links" aria-label={`${row.title} capabilities`}>
      {row.links.map((link, linkIndex) => <button key={link} className={active === linkIndex ? 'active' : ''} onMouseEnter={() => setActive(linkIndex)} onFocus={() => setActive(linkIndex)} onClick={() => setActive(linkIndex)}>{link}<ArrowUpRight size={15} /></button>)}
    </div>
  </article>
}

function App() {
  const [scrolled, setScrolled] = useState(false)
  const [darkNav, setDarkNav] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [activeProof, setActiveProof] = useState(0)
  const [routePlaying, setRoutePlaying] = useState(true)
  const mapRef = useRef<SVGSVGElement>(null)

  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) setRoutePlaying(false)
  }, [])

  useEffect(() => {
    const svg = mapRef.current
    if (!svg) return
    if (routePlaying) svg.unpauseAnimations()
    else svg.pauseAnimations()
  }, [routePlaying])

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 48)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    const footer = document.querySelector('.dark-footer')
    const observer = footer ? new IntersectionObserver(([entry]) => setDarkNav(entry.isIntersecting), { threshold: 0.08 }) : null
    if (footer && observer) observer.observe(footer)
    return () => { window.removeEventListener('scroll', onScroll); observer?.disconnect() }
  }, [])

  const navClass = `${scrolled ? 'is-scrolled' : ''} ${darkNav ? 'on-dark' : ''}`

  return <main id="top" className="landing">
    <div className={`top-blur ${darkNav ? 'on-dark' : ''}`} aria-hidden="true" />
    <header className={`site-nav-fixed ${navClass}`}>
      <div className="nav-inner">
        <div className="nav-left"><Brand markOnly={scrolled} /><nav aria-label="Main navigation"><a href="#what-we-do">What we do</a><a href="#who-we-serve">Who we serve</a><a href="#approach">About us</a></nav></div>
        <nav className="nav-right" aria-label="Project links"><a href="#get-started">Project plan</a><a href="#get-started">Explore</a><a href="#footer-contact">Contact</a></nav>
        <button className="mobile-menu" onClick={() => setMenuOpen(!menuOpen)} aria-expanded={menuOpen} aria-label={menuOpen ? 'Close menu' : 'Open menu'}>{menuOpen ? <X size={16} /> : <Menu size={16} />}<span>{menuOpen ? 'Close' : 'Menu'}</span></button>
      </div>
      {menuOpen && <div className="mobile-nav-panel"><a href="#what-we-do" onClick={() => setMenuOpen(false)}>What we do</a><a href="#who-we-serve" onClick={() => setMenuOpen(false)}>Who we serve</a><a href="#approach" onClick={() => setMenuOpen(false)}>About us</a><a href="#get-started" onClick={() => setMenuOpen(false)}>Explore the project</a></div>}
    </header>

    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-plates" aria-label="Illustrative PeriShift product interface cards">
        {plates.map(plate => <FloatingPlate key={plate.place} plate={plate} />)}
      </div>
      <div className="hero-center">
        <span className="hero-overline">TIME-SENSITIVE AID, THOUGHTFULLY COORDINATED</span>
        <h1 id="hero-title">The last mile for<br />good food.</h1>
        <p>Move perishable donations with a clearer view of time, people, and receiving capacity—right up to the handoff.</p>
        <div className="hero-buttons"><a className="button-primary" href="#get-started">Explore the project <ArrowRight size={14} /></a><a className="button-secondary" href="#what-we-do">See how it works</a></div>
      </div>
      <div className="hero-base-note"><span>PERISHIFT · PROJECT PREVIEW</span><span>DONOR <i>→</i> RIDER <i>→</i> RECEIVER</span></div>
      <div className="hero-scroll-cue"><span /> SCROLL TO EXPLORE</div>
    </section>

    <section className="trust-strip" aria-label="Network roles">
      <div className="marquee" aria-hidden="true"><div className="marquee-track">{Array.from({ length: 2 }).map((_, repeat) => <span key={repeat} className="marquee-group"><b>DONORS</b><i>✳</i><b>VOLUNTEER RIDERS</b><i>✳</i><b>RECEIVING PARTNERS</b><i>✳</i><b>COORDINATORS</b><i>✳</i></span>)}</div></div>
      <div className="trust-intro"><span className="section-mono">01 / A CONNECTED HANDOFF</span><p>Food can be ready and still miss the moment. PeriShift is designed to connect the people, timing, and capacity that make a safe handoff possible.</p></div>
      <div className="principle-strip">{[
        ['01', 'Deadline first', 'Time is part of every match.'],
        ['02', 'Capacity aware', 'Plans respect real quantities.'],
        ['03', 'Custody visible', 'Food stays accounted for.'],
        ['04', 'Recovery ready', 'Changes prompt a fresh check.'],
      ].map(([n, title, copy]) => <div className="principle-item" key={n}><span className="section-mono">{n}</span><b>{title}</b><small>{copy}</small></div>)}</div>
    </section>

    <section id="what-we-do" className="what-section">
      <div className="what-heading"><span className="section-mono">02 / THE WORKFLOW</span><h2>What we do</h2><p>One system for the details between surplus and a safe arrival.</p></div>
      <div className="work-rows">{workRows.map((row, index) => <WorkRow key={row.number} row={row} index={index} />)}</div>
    </section>

    <section id="route-demo" className="route-section">
      <div className="route-heading"><span className="section-mono">03 / ROUTE SIMULATION</span><h2>A handoff,<br />as it moves.</h2><p>See how a donation, a rider, and a receiving partner come together around one safe window.</p><span className="simulation-stamp"><i /> SIMULATED ROUTE · NOT LIVE GPS</span></div>
      <div className="dispatch-board">
        <div className="route-map-wrap">
          <div className="map-label map-label-origin"><MapPin size={14} /><span><b>North Kitchen</b><small>Pickup · 14:10</small></span></div>
          <div className="map-label map-label-destination"><HeartHandshake size={14} /><span><b>Good Table</b><small>Receiving partner</small></span></div>
          <svg ref={mapRef} className="route-map" viewBox="0 0 800 470" role="img" aria-label="Illustrated sample route with a rider moving from North Kitchen to Good Table">
            <defs><pattern id="mapGrid" width="38" height="38" patternUnits="userSpaceOnUse"><path d="M 38 0 L 0 0 0 38" fill="none" stroke="#dfe4dc" strokeWidth="1" /></pattern></defs>
            <rect width="800" height="470" fill="url(#mapGrid)" />
            <g className="map-blocks"><path d="M40 75h175v78H40zM260 39h103v95H260zM464 72h152v65H464zM641 38h106v106H641zM60 260h118v103H60zM227 312h137v91H227zM473 276h119v99H473zM636 246h119v122H636z" /><path d="M96 174h108M283 153v103M407 44v106M556 151v99M690 162v57M419 280v123M190 255h81M600 392h112" /></g>
            <path className="route-road" d="M123 120 C195 120 202 202 276 208 S365 168 417 210 S495 306 554 298 S650 276 688 320" />
            <path className="route-path" d="M123 120 C195 120 202 202 276 208 S365 168 417 210 S495 306 554 298 S650 276 688 320" />
            <circle className="map-node origin-node" cx="123" cy="120" r="9" /><circle className="map-node destination-node" cx="688" cy="320" r="9" />
            <g className="rider-marker"><circle r="14" /><Truck size={15} x={-7.5} y={-7.5} color="#fff" /><animateMotion dur="18s" repeatCount="indefinite" path="M123 120 C195 120 202 202 276 208 S365 168 417 210 S495 306 554 298 S650 276 688 320" /></g>
            <text x="364" y="259" className="map-distance">~18 MIN SAMPLE ROUTE</text>
          </svg>
          <div className="map-scale"><span /><span>Illustrative neighborhood map</span><b>1.8 km</b></div>
        </div>
        <aside className="route-status">
          <div className="status-top"><span className="section-mono">SAMPLE RUN · PS–0428</span><span className="status-live"><i /> DEMO</span></div>
          <h3>Fresh meals</h3><p className="status-qty">40 <span>portions · prepared 14:10</span></p>
          <div className="route-progress"><i /></div>
          <div className="route-stages"><div className="complete"><i><Check size={11} /></i><span><b>Donation listed</b><small>Ready for planning</small></span></div><div className="complete"><i><Check size={11} /></i><span><b>Rider accepted</b><small>Meera · volunteer rider</small></span></div><div className="current"><i /><span><b>Pickup confirmed</b><small>On the way to Good Table</small></span></div><div><i /><span><b>Receipt pending</b><small>30 portions requested</small></span></div></div>
          <div className="route-controls"><button type="button" onClick={() => setRoutePlaying(!routePlaying)} aria-pressed={routePlaying}>{routePlaying ? 'Pause route' : 'Play route'}</button><button type="button" onClick={() => { mapRef.current?.setCurrentTime(0); setRoutePlaying(true) }}>Replay <ArrowRight size={13} /></button></div>
          <small className="route-disclaimer">Movement and timing are simulated for this project preview.</small>
        </aside>
      </div>
    </section>

    <section id="who-we-serve" className="roles-section">
      <div className="roles-heading"><span className="section-mono">04 / WHO WE SERVE</span><h2>Made for every side<br />of the handoff.</h2><p>Different people hold different pieces of the plan. The experience should make each next step feel clear.</p><div className="carousel-arrows"><button aria-label="Previous roles" onClick={() => setActiveProof((activeProof + roles.length - 1) % roles.length)}><ChevronLeft size={17} /></button><button aria-label="Next roles" onClick={() => setActiveProof((activeProof + 1) % roles.length)}><ChevronRight size={17} /></button></div></div>
      <div className="role-cards">{[roles[activeProof], roles[(activeProof + 1) % roles.length]].map(role => { const Icon = role.icon; return <article className="role-card role-active" key={role.number}><span className="role-mark"><Icon size={18} /></span><span className="section-mono">{role.number}</span><h3>{role.title}</h3><p>{role.copy}</p><span className="role-bottom">PROJECT ROLE · SAMPLE FLOW</span><span className={`role-wash wash-${role.tone}`} /></article> })}</div>
      <div className="roles-caption"><span>DESIGNED AROUND FOUR PROJECT ROLES</span><span>NO CUSTOMER CLAIMS OR METRICS SHOWN</span></div>
    </section>

    <section id="get-started" className="start-section">
      <div className="start-heading"><div><span className="section-mono">05 / EXPLORE THE PROJECT</span><h2>Get started</h2></div><div className="start-actions"><a className="button-primary" href="#approach">Read the plan <ArrowUpRight size={14} /></a><a className="button-secondary" href="#what-we-do">Explore the flow</a></div></div>
      <div className="start-grid">{quickstart.map((item) => <article className="start-card" key={item.number}><span className="start-label">{item.label}</span><h3>{item.title}</h3><div className={`start-preview preview-${item.type}`}><div className="preview-glow" /><div className="app-window"><div className="window-top"><span className="mini-brand"><i /> peri<span>shift</span></span><span>•••</span></div>{item.type === 'donation' && <><small>NEW DONATION</small><b>Fresh meals</b><span>40 portions · ready 14:10</span><div className="window-progress"><i /></div><button>Review details</button></>}{item.type === 'match' && <><small>PLANNING VIEW</small><b>2 receiving options</b><span>Time · capacity · suitability</span><div className="window-bars"><i /><i /><i /></div></>}{item.type === 'rider' && <><small>ACTIVE HANDOFF</small><b>Pickup confirmed</b><span>North Kitchen → Good Table</span><div className="mini-route"><i /><i /><i /></div></>}{item.type === 'receipt' && <><small>RECEIPT RECORD</small><b>30 portions accepted</b><span>10 still accounted for</span><div className="window-check"><Check size={11} /> Quantity recorded</div></>}</div><span className="planned-note">PROJECT UI PREVIEW</span></div><span className="start-index">{item.number}</span></article>)}</div>
    </section>

    <section id="approach" className="approach-section"><span className="section-mono">06 / A PRACTICAL PROMISE</span><p>Make the next handoff <em>clear enough to trust.</em></p><div className="approach-copy"><span>PeriShift is a project prototype. Routes are estimates, sample views are illustrative, and safety depends on verified information and real-world handling.</span><a href="#what-we-do">Explore the workflow <ArrowRight size={14} /></a></div></section>

    <footer className="dark-footer" id="footer-contact">
      <div className="footer-inner"><a className="footer-talk" href="#get-started">Let’s move good food <ArrowRight size={31} strokeWidth={1.2} /></a>
        <div className="footer-columns">{[
          { label: 'THE WORKFLOW', links: ['Donation intake', 'Matching', 'Pickup', 'Delivery', 'Recovery'] },
          { label: 'WHO WE SERVE', links: ['Donors', 'Volunteer riders', 'Receiving partners', 'Coordinators'] },
          { label: 'ABOUT PERISHIFT', links: ['Our approach', 'Safety principles', 'Project overview'] },
          { label: 'PROJECT', links: ['Implementation plan', 'Sample flow', 'Current prototype'] },
          { label: 'TRANSPARENCY', links: ['Estimated routes', 'Illustrative data', 'Known limitations'] },
          { label: 'GET STARTED', links: ['Explore the workflow', 'View project plan', 'Contact the team'] },
        ].map((column, index) => <div className="footer-column" key={column.label}><span>{column.label}</span><div>{column.links.map((link, linkIndex) => <a key={link} href={index === 0 ? '#what-we-do' : index === 5 ? '#get-started' : '#approach'}>{link}{linkIndex === column.links.length - 1 && <ArrowUpRight size={12} />}</a>)}</div><small className="section-mono">Ø{index + 1}</small></div>)}</div>
        <div className="footer-wordmark" aria-hidden="true">perishift</div>
      </div>
    </footer>
  </main>
}

export default App
