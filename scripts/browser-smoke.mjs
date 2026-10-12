// Chromium QA against isolated test servers. Credentials arrive on stdin only.
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
let input = '';
for await (const chunk of process.stdin) input += chunk;
const credentials = JSON.parse(input);
input = '';
const profile = await mkdtemp(join(tmpdir(), 'sentinel-browser-'));
const browser = spawn('/usr/bin/chromium', ['--headless=new', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--no-first-run', '--no-default-browser-check', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=18767', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
let socket;
const pending = new Map();
let sequence = 0;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function retry(check) {
  for (let i = 0; i < 100; i++) { try { if (await check()) return; } catch {} await pause(100); }
  throw new Error('Browser check timed out');
}
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000);
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true });
  if (result.exceptionDetails) throw new Error('Browser evaluation failed');
  return result.result.value;
}
async function click(text) {
  await evaluate(`Array.from(document.querySelectorAll('button')).find(b => b.textContent === ${JSON.stringify(text)})?.click()`);
}
try {
  let target;
  await retry(async () => { const targets = await (await fetch('http://127.0.0.1:18767/json/list')).json(); target = targets.find(t => t.type === 'page'); return !!target; });
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id); clearTimeout(request.timer);
    if (message.error) request.reject(new Error(message.error.message)); else request.resolve(message.result);
  });
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await call('Emulation.setFocusEmulationEnabled', { enabled: true });
  await call('Page.navigate', { url: 'http://localhost:18766' });
  await retry(() => evaluate(`!!document.querySelector('input[name="password"]')`));
  await evaluate(`document.querySelector('input[name="username"]').value=${JSON.stringify(credentials.username)};document.querySelector('input[name="password"]').value=${JSON.stringify(credentials.password)};document.querySelector('form').requestSubmit()`);
  delete credentials.password;
  await retry(() => evaluate(`document.querySelector('h1')?.textContent === 'Browser Test Laptop'`));
  await retry(() => evaluate(`document.body.innerText.includes('Location unavailable') && document.body.innerText.includes('Online')`));
  if (!await evaluate('document.documentElement.scrollWidth <= window.innerWidth')) throw new Error('Mobile layout overflows horizontally');
  // Trusted loopback reverse-proxy header for this isolated QA backend only.
  const positioned = await evaluate(`fetch('/api/agent/location/wifi', {method:'POST', headers:{'Content-Type':'application/json','Authorization':'Bearer '+${JSON.stringify(credentials.deviceCredential)},'X-Forwarded-Proto':'https'}, body:JSON.stringify({consent:true,scanned_at:Math.floor(Date.now()/1000),access_points:[{bssid:'f0:d5:bf:fd:12:ae'},{bssid:'30:86:2d:c4:29:d0'}]})}).then(r=>r.json()).then(r=>r.status)`);
  delete credentials.deviceCredential;
  if (positioned !== 'available') throw new Error('Mocked Google positioning failed');
  await click('Refresh');
  await retry(() => evaluate(`document.body.innerText.includes('-12.968') && document.body.innerText.includes('28.636') && document.body.innerText.includes('42.5 m')`));
  if (!await evaluate(`!!document.querySelector('.google-attribution[translate="no"]') && !document.querySelector('.map') && !Array.from(document.querySelectorAll('button')).some(b=>b.textContent==='Show interactive map')`)) throw new Error('Google attribution/map policy failed');
  await call('Browser.grantPermissions', { origin: 'http://localhost:18766', permissions: ['clipboardReadWrite', 'clipboardSanitizedWrite'] });
  await click('Copy Coordinates');
  await retry(() => evaluate(`document.body.innerText.includes('Coordinates copied.')`));
  if (await evaluate('navigator.clipboard.readText()') !== '-12.968, 28.636') throw new Error('Coordinate copy mismatch');
  if (!await evaluate('document.documentElement.scrollWidth <= window.innerWidth')) throw new Error('Location card overflows on mobile');
  await click('Lock Laptop');
  await retry(() => evaluate(`!!document.querySelector('[role="dialog"]')`));
  await click('Cancel');
  await retry(() => evaluate(`!document.querySelector('[role="dialog"]')`));
  if (await evaluate(`document.body.innerText.includes('Last lock:')`)) throw new Error('Cancelled lock unexpectedly created a command');
  await click('Lock Laptop'); await click('Confirm lock');
  await retry(() => evaluate(`document.body.innerText.includes('Last lock: pending')`));
  const permission = await evaluate(`document.querySelector('summary').textContent`);
  if (!permission.includes('permissions')) throw new Error('Monitoring limitations control missing');
  await retry(() => evaluate(`navigator.serviceWorker.controller !== null`));
  const manifest = await evaluate(`fetch('/manifest.webmanifest').then(r=>r.json()).then(m=>m.name)`);
  if (manifest !== 'Sentinel Laptop Security') throw new Error('Manifest mismatch');
  await click('Sign out');
  await retry(() => evaluate(`!!document.querySelector('input[name="password"]')`));
  if (await evaluate(`document.body.innerText.includes('Browser Test Laptop')`)) throw new Error('Owner data remained visible after logout');
  console.log('PASS: mobile Chromium login, unavailable then mocked Google coordinates/radius, attribution without non-Google map, coordinate copy, no overflow, lock confirmation, PWA + logout privacy');
} finally {
  socket?.close();
  browser.kill('SIGTERM');
  await new Promise(resolve => { if (browser.exitCode !== null) resolve(); else { browser.once('exit', resolve); setTimeout(() => { browser.kill('SIGKILL'); resolve(); }, 3000).unref(); } });
  await rm(profile, { recursive: true, force: true });
}
