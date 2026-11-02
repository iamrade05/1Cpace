const { test } = require('node:test');
const assert = require('node:assert/strict');
const { workspaceOrigin, navigationAction, readPrintMessage } = require('../src/workspacePolicy.ts');
const origin = 'https://www.1cpace.co.za';

test('workspace origins require HTTPS and cannot embed credentials or paths', () => {
  assert.equal(workspaceOrigin(origin + '/'), origin);
  for (const url of ['http://www.1cpace.co.za', origin + '/login', origin + '?token=x', 'https://user:password@www.1cpace.co.za', origin + '#x']) assert.throws(() => workspaceOrigin(url));
});

test('navigation keeps actual gym routes in the workspace', () => {
  for (const path of ['/login', '/login/verify', '/hr/?week=2026-10-12', '/members/12/documents/3/download', '/training/manual?format=pdf#page=7']) assert.equal(navigationAction(origin + path, origin), 'internal');
});

test('external links use separate applications and deceptive URLs cannot become internal', () => {
  assert.equal(navigationAction(origin + '/static/downloads/1Cpace-Android-1.1.0.apk', origin), 'external');
  for (const url of ['https://wa.me/27000000000', 'mailto:gym@example.com', 'tel:0123456789', 'sms:0123456789', 'whatsapp://send?text=Hello']) assert.equal(navigationAction(url, origin), 'external');
  for (const url of ['https://www.1cpace.co.za.example.com', 'https://other.example/www.1cpace.co.za', origin + ':8443']) assert.equal(navigationAction(url, origin), 'external');
  for (const url of ['javascript:alert(1)', 'file:///etc/passwd', 'data:text/html,test', 'intent://example', 'http://www.1cpace.co.za', 'https://user@www.1cpace.co.za', '/login']) assert.equal(navigationAction(url, origin), 'blocked');
});

test('printing accepts only bounded messages from the currently displayed trusted page', () => {
  const url = origin + '/contracts/12';
  const message = JSON.stringify({ type: 'print', url, html: '<!doctype html><p>Signed contract</p>' });
  assert.deepEqual(readPrintMessage(message, url, origin), { html: '<!doctype html><p>Signed contract</p>' });
  assert.equal(readPrintMessage(message, 'https://other.example', origin), null);
  assert.equal(readPrintMessage(message, origin + '/contracts/13', origin), null);
  for (const data of ['invalid JSON', '{}', JSON.stringify({ type: 'print', url, html: '' }), JSON.stringify({ type: 'print', url, html: 'a'.repeat(8_000_001) })]) assert.equal(readPrintMessage(data, url, origin), null);
});

test('iOS downloads accept only base64 files fetched by the currently displayed trusted page', () => {
  const { readDownloadMessage, safeFileName } = require('../src/workspacePolicy.ts');
  const page = origin + '/members/12';
  const file = origin + '/members/12/documents/3/download';
  const message = fields => JSON.stringify({ type: 'download', url: file, page, name: 'ID copy.pdf', mime: 'application/pdf', data: 'JVBERi0=', ...fields });
  assert.deepEqual(readDownloadMessage(message({}), page, origin), { name: 'ID copy.pdf', mime: 'application/pdf', data: 'JVBERi0=' });
  assert.equal(readDownloadMessage(message({}), 'https://other.example', origin), null);
  assert.equal(readDownloadMessage(message({}), origin + '/members/13', origin), null);
  for (const url of ['https://other.example/file.pdf', 'file:///etc/passwd']) assert.equal(readDownloadMessage(message({ url }), page, origin), null);
  for (const data of ['', 'not base64!', 7]) assert.equal(readDownloadMessage(message({ data }), page, origin), null);
  assert.equal(readDownloadMessage(message({ mime: '<script>' }), page, origin).mime, 'application/octet-stream');
  assert.equal(readDownloadMessage('invalid JSON', page, origin), null);
  assert.equal(safeFileName('../../Library/evil.sh'), '_.._Library_evil.sh');
  assert.equal(safeFileName('a/b\\c:d'), 'a_b_c_d');
  for (const name of ['', '...', null]) assert.equal(safeFileName(name), 'download');
});
