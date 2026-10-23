const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../../artifacts/apk');
const apk = path.join(root, '1Cpace-Android.apk');
if (!fs.existsSync(apk)) throw new Error('Build and sign the APK before starting the download server.');
const page = `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Install 1Cpace</title><style>body{font:18px system-ui;background:#f5f7f4;color:#123127;margin:0;padding:32px 24px;max-width:540px}h1{font-size:36px}a{display:block;background:#15392f;color:white;padding:18px;border-radius:12px;text-align:center;text-decoration:none;font-weight:700}p{line-height:1.5}small{color:#4b6258}</style><h1>1Cpace for Android</h1><p>Staff and member access in a standalone app.</p><a href="/1Cpace-Android.apk">Download Android APK</a><p>Open the downloaded APK to install. Android may ask you to allow installation from your browser.</p><small>Keep this phone and the development PC on the same Wi-Fi during the download. After installation, the app connects directly to your gym over the internet.</small></html>`;
http.createServer((request, response) => {
  if (!['GET', 'HEAD'].includes(request.method)) {
    response.writeHead(405); response.end(); return;
  }
  const pathname = new URL(request.url, 'http://localhost').pathname;
  response.setHeader('Cache-Control', 'no-store');
  if (pathname === '/') {
    response.setHeader('Content-Type', 'text/html; charset=utf-8');
    response.end(request.method === 'HEAD' ? undefined : page); return;
  }
  const files = {
    '/1Cpace-Android.apk': [apk, 'application/vnd.android.package-archive'],
    '/qr.png': [path.join(root, 'download-qr.png'), 'image/png'],
  };
  const item = files[pathname];
  if (!item || !fs.existsSync(item[0])) { response.writeHead(404); response.end(); return; }
  response.setHeader('Content-Type', item[1]);
  response.setHeader('Content-Length', fs.statSync(item[0]).size);
  if (pathname.endsWith('.apk')) response.setHeader('Content-Disposition', 'attachment; filename="1Cpace-Android.apk"');
  if (request.method === 'HEAD') { response.end(); return; }
  fs.createReadStream(item[0]).pipe(response);
}).listen(8088, '0.0.0.0', () => process.stdout.write('APK download server listening on port 8088\n'));
