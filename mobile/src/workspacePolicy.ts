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

export function readPrintMessage(data: string, sourceUrl: string, origin: string): { html: string } | null {
  if (navigationAction(sourceUrl, origin) !== 'internal' || data.length > 8_000_000) return null;
  try {
    const message = JSON.parse(data);
    if (message.type !== 'print' || typeof message.html !== 'string' || !message.html.trim()) return null;
    if (message.url !== sourceUrl || navigationAction(message.url, origin) !== 'internal') return null;
    return { html: message.html };
  } catch { return null; }
}
