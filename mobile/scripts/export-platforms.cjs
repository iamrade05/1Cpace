const { execFileSync } = require('node:child_process');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const cli = path.join(root, 'node_modules', 'expo', 'bin', 'cli');
const env = { ...process.env, CI: '1', NODE_OPTIONS: `${process.env.NODE_OPTIONS || ''} --max-old-space-size=512`.trim() };
delete env.FORCE_COLOR;
for (const platform of ['web', 'android', 'ios']) {
  const destination = platform === 'web' ? 'dist' : `dist-${platform}`;
  console.log(`\nExporting ${platform} to ${destination} (one worker).`);
  execFileSync(process.execPath, [cli, 'export', '--platform', platform, '--max-workers', '1', '--output-dir', destination], {
    cwd: root, env, stdio: 'inherit',
  });
}
