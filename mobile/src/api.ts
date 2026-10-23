import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
export type Kind = 'member' | 'staff';
export type Item = { id: number; title: string; subtitle: string; status: string; detail?: string; booked?: boolean };
export type Home = { kind: Kind; name: string; gym: string; features: string[]; reference?: string; membership?: string; package?: string; monthly_fee?: number; access?: string; department?: string; role?: string };
export type Session = { base: string; gym: string; token: string; kind: Kind; demo?: boolean };
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
const key = 'onecpase.mobile.session.v1';
export function serverUrl(value: string) {
  const url = new URL(value.trim());
  if (url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Error('Enter the server address without a path or password.');
  if (url.protocol !== 'https:' && !(__DEV__ && url.protocol === 'http:')) throw new Error('Your gym server must use HTTPS.');
  return url.origin;
}
export async function request<T>(session: Pick<Session, 'base' | 'gym'> & Partial<Session>, path: string, body?: unknown): Promise<T> {
  const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(`${session.base}/api/mobile/v1/${encodeURIComponent(session.gym)}${path}`, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'omit', signal: controller.signal,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data: any;
    try { data = JSON.parse(await response.text()); } catch { throw new ApiError(response.status === 404 ? 'Mobile access is not available at your gym yet. Please contact reception.' : 'Your gym could not load this information. Please try again.', response.status); }
    if (!response.ok) throw new ApiError(data.error || data.message || 'This action is unavailable.', response.status);
    return data as T;
  } catch (e) {
    if (e instanceof Error && e.name === 'AbortError') throw new Error('Connection timed out. Try again.');
    throw e instanceof Error ? e : new Error('Could not connect. Check your connection.');
  } finally { clearTimeout(timer); }
}
export async function saveSession(session: Session | null) {
  if (Platform.OS === 'web') return; // Browser preview never persists credentials.
  if (session && !session.demo) await SecureStore.setItemAsync(key, JSON.stringify(session), { keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY });
  else await SecureStore.deleteItemAsync(key);
}
export async function loadSession(): Promise<Session | null> {
  if (Platform.OS === 'web') return null;
  const value = await SecureStore.getItemAsync(key); if (!value) return null;
  try {
    const saved = JSON.parse(value);
    if (!saved.token || !saved.gym || !['member','staff'].includes(saved.kind)) throw new Error('Invalid session');
    return { ...saved, base: serverUrl(saved.base), demo: false };
  } catch { await SecureStore.deleteItemAsync(key); return null; }
}
