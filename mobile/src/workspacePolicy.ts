export type NavigationAction = 'internal' | 'external' | 'blocked';

export function workspaceOrigin(value: string): string {
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('The staff workspace needs an HTTPS gym server address.');
  }
  return url.origin;
}

export function navigationAction(value: string, origin: string): NavigationAction {
  try {
    const url = new URL(value);
    if (url.username || url.password) return 'blocked';
    if (url.protocol === 'https:') {
      if (url.origin !== origin) return 'external';
      // Let the browser own APK installation instead of requesting installer
      // privileges in the gym app. Protected documents still stay in WebView.
      if (url.pathname.startsWith('/static/downloads/') && url.pathname.toLowerCase().endsWith('.apk')) return 'external';
      return 'internal';
    }
    if (['tel:', 'mailto:', 'sms:', 'whatsapp:'].includes(url.protocol)) return 'external';
  } catch { /* Malformed and relative navigation requests fail closed. */ }
  return 'blocked';
}

// iOS cannot hand protected files to a system download manager, so the page
// fetches them with its own session and the app saves them for sharing.
export const DOWNLOAD_LIMIT_BYTES = 20_000_000;

export type DownloadMessage = { name: string; mime: string; data: string };

export function safeFileName(value: unknown): string {
  const name = typeof value === 'string' ? value.replace(/[\u0000-\u001f\\/:*?"<>|]/g, '_').replace(/^\.+/, '').trim() : '';
  return name.slice(-120) || 'download';
}

export function readDownloadMessage(data: string, sourceUrl: string, origin: string): DownloadMessage | null {
  if (navigationAction(sourceUrl, origin) !== 'internal' || data.length > DOWNLOAD_LIMIT_BYTES * 1.4) return null;
  try {
    const message = JSON.parse(data);
    if (message.type !== 'download' || message.page !== sourceUrl || navigationAction(message.url, origin) !== 'internal') return null;
    if (typeof message.data !== 'string' || !message.data || !/^[A-Za-z0-9+/]+=*$/.test(message.data)) return null;
    const mime = typeof message.mime === 'string' && /^[\w.+-]+\/[\w.+-]+$/.test(message.mime) ? message.mime : 'application/octet-stream';
    return { name: safeFileName(message.name), mime, data: message.data };
  } catch { return null; }
}

export function readPrintMessage(data: string, sourceUrl: string, origin: string): { html: string } | null {
  if (navigationAction(sourceUrl, origin) !== 'internal' || data.length > 8_000_000) return null;
  try {
    const message = JSON.parse(data);
    if (message.type !== 'print' || typeof message.html !== 'string' || !message.html.trim()) return null;
    if (message.url !== sourceUrl || navigationAction(message.url, origin) !== 'internal') return null;
    return { html: message.html };
  } catch { return null; }
}
