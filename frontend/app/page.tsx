'use client';
import dynamic from 'next/dynamic';
import { FormEvent, useEffect, useRef, useState } from 'react';
import { api, ApiError, Device, setCsrf, time } from '../lib/api';
const LocationMap = dynamic(() => import('../components/LocationMap'), { ssr: false, loading: () => <p>Loading map…</p> });

export default function Home() {
  const [user, setUser] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [device, setDevice] = useState<Device | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [reachable, setReachable] = useState(false);
  const [checked, setChecked] = useState<number | null>(null);
  const [pair, setPair] = useState<{ token: string; expires_at: number } | null>(null);
  const [confirmLock, setConfirmLock] = useState(false);
  const [confirmRevoke, setConfirmRevoke] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const [clock, setClock] = useState(0);
  const polling = useRef(false);

  async function refresh() {
    if (polling.current) return;
    polling.current = true;
    try {
      const result = await api<{ device: Device | null; server_time: number }>('/device');
      setDevice(result.device); setChecked(Date.now()); setReachable(true); setError('');
      if (result.device) setPair(null);
    } catch (e) {
      setReachable(false);
      if (e instanceof ApiError && e.status === 401) { setUser(null); setDevice(null); setPair(null); setCsrf(''); }
      else setError('Dashboard unreachable. Displayed data is from the last successful refresh.');
    } finally { polling.current = false; }
  }

  useEffect(() => {
    if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
    api<{ username: string; csrf: string }>('/auth/me').then(result => {
      setCsrf(result.csrf); setUser(result.username);
    }).catch(e => { if (!(e instanceof ApiError && e.status === 401)) setError('Cannot reach Sentinel. Check the server connection.'); }).finally(() => setReady(true));
  }, []);

  useEffect(() => {
    if (!user) return;
    void refresh();
    const clockTimer = setInterval(() => setClock(Date.now()), 5000);
    const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 30000);
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => { clearInterval(timer); clearInterval(clockTimer); document.removeEventListener('visibilitychange', onVisible); };
  }, [user]);

  useEffect(() => {
    if (!confirmLock && !confirmRevoke) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const handleKeys = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) { setConfirmLock(false); setConfirmRevoke(false); }
      if (event.key !== 'Tab') return;
      const controls = document.querySelectorAll<HTMLButtonElement>('.modal button:not(:disabled)');
      const first = controls[0], last = controls[controls.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', handleKeys);
    return () => { document.removeEventListener('keydown', handleKeys); previousFocus?.focus(); };
  }, [confirmLock, confirmRevoke, busy]);

  async function act(work: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('');
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : 'Action failed. Please retry.'); }
    finally { setBusy(false); }
  }

  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    void act(async () => {
      const result = await api<{ username: string; csrf: string }>('/auth/login', 'POST', { username: form.get('username'), password: form.get('password') });
      setCsrf(result.csrf); setUser(result.username);
    });
  }

  async function push() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) throw new Error('Web Push is unavailable in this browser.');
    const { public_key, configured } = await api<{ public_key: string; configured: boolean }>('/push/key');
    if (!configured) throw new Error('Web Push requires VAPID keys on the server. In-app alerts remain available.');
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') throw new Error('Notification permission was not granted.');
    const registration = await navigator.serviceWorker.ready;
    const base64 = public_key.replace(/-/g, '+').replace(/_/g, '/');
    const bytes = Uint8Array.from(atob(base64 + '='.repeat((4 - base64.length % 4) % 4)), c => c.charCodeAt(0));
    const subscription = await registration.pushManager.getSubscription() || await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: bytes });
    const data = subscription.toJSON();
    await api('/push/subscription', 'POST', { endpoint: data.endpoint, keys: data.keys });
    setNotice('Notifications enabled on this phone.');
  }

  const current = reachable && checked !== null && Math.max(clock, Date.now()) - checked < 45000;
  const displayStatus = current ? device?.status : 'unreachable';
  const latest = device?.commands[0];
  return <main>
    <header><a className="brand" href="/" aria-label="Sentinel home"><span className="shield" aria-hidden="true">S</span><span>Sentinel<small>Personal laptop security</small></span></a>
      {user && <button className="text-button" disabled={busy} onClick={() => void act(async () => { await api('/auth/logout', 'POST'); setUser(null); setDevice(null); setPair(null); setCsrf(''); })}>Sign out</button>}
    </header>
    {error && <div className="banner error" role="alert">{error}</div>}
    {notice && <div className="banner" role="status">{notice}</div>}
    {!ready ? <section className="card"><p>Connecting to Sentinel…</p></section> : !user ? <section className="card login">
      <p className="eyebrow">OWNER ACCESS</p><h1>Your laptop, within reach.</h1><p className="muted">Sign in to check its status and manage protection.</p>
      <form onSubmit={login}><label>Username<input name="username" autoComplete="username" maxLength={64} required /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" maxLength={256} required /></label>
        <button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</button></form>
      <p className="muted small">Use the owner account created during server setup.</p>
    </section> : <>
      <div className="section-heading"><div><p className="eyebrow">YOUR PROTECTED DEVICE</p><h1>{device?.name || 'Pair your laptop'}</h1></div><button className="text-button" disabled={busy} onClick={() => void refresh()}>Refresh</button></div>
      {!device ? <section className="card"><h2>Connect Sentinel Agent</h2><p>Generate a one-time pairing token, then enter it into the agent on your Kali laptop. Tokens expire after 10 minutes.</p>
        <button className="primary" disabled={busy} onClick={() => void act(async () => setPair(await api('/enrollment', 'POST')))}>Generate pairing token</button>
        {pair && <div className="pair"><label>One-time token<input readOnly value={pair.token} onFocus={e => e.currentTarget.select()} /></label><p className="small">Expires {time(pair.expires_at)}. Keep this token private.</p>
          <button onClick={() => void act(async () => { await navigator.clipboard.writeText(pair.token); setNotice('Pairing token copied.'); })}>Copy token</button></div>}
      </section> : <>
        <section className="card overview"><div className="status-line"><span className={`status ${displayStatus}`}><i />{displayStatus === 'online' ? 'Online' : displayStatus === 'stale' ? 'Stale contact' : displayStatus === 'offline' ? 'Offline / unreachable' : 'Unreachable'}</span><span className="muted small">60-second heartbeat</span></div>
          <dl><dt>Last seen</dt><dd>{time(device.last_seen)}</dd><dt>Dashboard checked</dt><dd>{checked ? new Date(checked).toLocaleTimeString() : 'Waiting…'}</dd></dl>
          <p className="muted small">A missed heartbeat can mean sleep, loss of power, or a network interruption.</p>
          <button className="primary lock" disabled={busy || !current} onClick={() => setConfirmLock(true)}>Lock Laptop</button>
          {latest && <div className="command" role="status"><strong>Last lock: {latest.status}</strong><span>{latest.detail || (latest.status === 'pending' ? 'Waiting for the laptop to connect.' : 'Delivered; waiting for a verified result.')}</span><small>{time(latest.issued_at)} · expires {time(latest.expires_at)}</small></div>}
        </section>
        <section className="card"><div className="card-heading"><h2>Last-known location</h2><span className="muted small">Approximate</span></div>
          {device.location ? <>
            {device.location_status !== 'available' && <p className="banner">Current location {device.location_status === 'disabled' ? 'collection is disabled' : 'unavailable'}. Showing the last valid fix.</p>}
            <dl className="location-values"><dt>Latitude</dt><dd>{device.location.latitude}</dd><dt>Longitude</dt><dd>{device.location.longitude}</dd>
              <dt>Accuracy radius</dt><dd>{device.location.accuracy_m === null ? (device.location.source === 'google_wifi' ? 'Unknown — radius not retained under the default Google storage policy' : 'Unknown — provider gives no estimate') : `${device.location.accuracy_m.toLocaleString()} m (provider estimate)`}</dd>
              <dt>Location source</dt><dd>{device.location.source === 'google_wifi' ? 'Google Maps Geolocation · Wi-Fi · IP fallback disabled' : device.location.source === 'geoclue' ? 'GeoClue · provider-dependent positioning' : 'IP fallback · may identify a VPN or ISP'}</dd>
              <dt>Last updated</dt><dd>{time(device.location.timestamp)}</dd>
            </dl>
            <p className="muted small">{device.location.accuracy_m === null ? 'Precision unknown.' : device.location.accuracy_m <= 100 ? 'Precise estimate (reported radius ≤100 m). Accuracy is not guaranteed.' : 'Coarse estimate (reported radius >100 m). Accuracy is not guaranteed.'}</p>
            <button disabled={busy} onClick={() => void act(async () => { const fix = device.location; if (!fix) return; await navigator.clipboard.writeText(`${fix.latitude}, ${fix.longitude}`); setNotice('Coordinates copied.'); })}>Copy Coordinates</button>
            {device.location.source === 'google_wifi' ? <p className="google-attribution" translate="no">Google Maps</p> : showMap ? <LocationMap location={device.location} /> : <div className="map-consent"><p className="muted small">Loading the map sends map tile requests for this area to OpenStreetMap.</p><button onClick={() => setShowMap(true)}>Show interactive map</button></div>}
          </> : <div className="empty"><h3>Location unavailable</h3><p>No valid position is available. Location needs owner consent and suitable signals on the laptop.</p></div>}
          {device.location_error && <p className="banner" role="status">{device.location_error}</p>}
        </section>
        <section className="card"><div className="card-heading"><h2>Security alerts</h2><span className="count">{device.alerts.length}</span></div>
          {device.alerts.length ? <ul className="alerts">{device.alerts.map(alert => <li key={alert.id}><strong>{alert.kind === 'auth_failures' ? 'Repeated authentication failures' : 'Network changed'}</strong><p>{alert.message}</p><small>{time(alert.occurred_at)} · push: {alert.push_status.replaceAll('_', ' ')}</small></li>)}</ul> : <div className="empty"><p>No security alerts received.</p></div>}
          <div className="button-row"><button disabled={busy} onClick={() => void act(push)}>Enable phone notifications</button><button disabled={busy} className="text-button" onClick={() => void act(async () => {
            await api('/push/subscription', 'DELETE');
            if ('serviceWorker' in navigator) { const registration = await navigator.serviceWorker.ready; const subscription = await registration.pushManager.getSubscription(); await subscription?.unsubscribe(); }
            setNotice('Push subscriptions removed for this owner. In-app alerts remain available.');
          })}>Disable push</button></div>
        </section>
        <details className="card"><summary>Agent permissions and monitoring</summary><dl className="capabilities">{Object.entries(device.capabilities).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}</dl><button className="danger-text" disabled={busy} onClick={() => setConfirmRevoke(true)}>Revoke laptop credentials</button></details>
      </>}
    </>}
    {confirmLock && <div className="modal-backdrop"><section className="modal card" role="dialog" aria-modal="true" aria-labelledby="lock-title"><h2 id="lock-title">Lock this laptop?</h2><p>The agent will request a GNOME screen lock. An offline laptop must reconnect within two minutes, or the request will expire.</p><div className="button-row"><button autoFocus disabled={busy} onClick={() => setConfirmLock(false)}>Cancel</button><button className="primary" disabled={busy} onClick={() => void act(async () => { if (!device) return; await api(`/devices/${device.id}/lock`, 'POST', { confirmed: true }); setConfirmLock(false); setNotice('Lock requested. Waiting for the agent’s verified result.'); await refresh(); })}>Confirm lock</button></div></section></div>}
    {confirmRevoke && <div className="modal-backdrop"><section className="modal card" role="dialog" aria-modal="true" aria-labelledby="revoke-title"><h2 id="revoke-title">Revoke this laptop?</h2><p>This disconnects the agent and removes its location and alerts from Sentinel. You must enroll it again to resume protection.</p><div className="button-row"><button autoFocus onClick={() => setConfirmRevoke(false)}>Cancel</button><button className="primary" disabled={busy} onClick={() => void act(async () => { if (!device) return; await api(`/devices/${device.id}`, 'DELETE'); setConfirmRevoke(false); setShowMap(false); await refresh(); })}>Revoke</button></div></section></div>}
    <footer>Sentinel v1.0 · Private by design<br /><a href="/privacy">Privacy</a> · <a href="/terms">Terms</a></footer>
  </main>;
}
