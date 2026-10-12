export type Location = { latitude: number; longitude: number; accuracy_m: number | null; source: 'geoclue' | 'ip_coarse' | 'google_wifi'; timestamp: number };
export type Command = { id: string; status: 'pending' | 'delivered' | 'successful' | 'failed' | 'expired'; issued_at: number; expires_at: number; detail: string };
export type Alert = { id: string; kind: string; occurred_at: number; message: string; push_status: string };
export type Device = { id: string; name: string; status: 'online' | 'stale' | 'offline' | 'unreachable'; last_seen: number | null; location: Location | null; location_status: string; location_error?: string; capabilities: Record<string, string>; commands: Command[]; alerts: Alert[] };
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method, credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000),
    headers: { 'Content-Type': 'application/json', ...(method !== 'GET' ? { 'X-CSRF-Token': csrf } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, typeof result.detail === 'string' ? result.detail : `Request failed (${response.status})`);
  return result as T;
}
export function time(value: number | null): string {
  return value ? new Date(value * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'No contact yet';
}
