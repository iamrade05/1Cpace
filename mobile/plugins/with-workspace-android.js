const { withAndroidManifest } = require('expo/config-plugins');

module.exports = function withWorkspaceAndroid(config) {
  return withAndroidManifest(config, mod => {
    const manifest = mod.modResults.manifest;
    manifest.queries ||= [];
    const queries = manifest.queries[0] ||= {};
    queries.intent ||= [];
    for (const name of ['android.media.action.IMAGE_CAPTURE', 'android.media.action.VIDEO_CAPTURE']) {
      if (!queries.intent.some(intent => intent.action?.some(action => action.$['android:name'] === name))) {
        queries.intent.push({ action: [{ $: { 'android:name': name } }] });
      }
    }
    // Android 7–9 need this for WebView's system DownloadManager. Newer
    // versions use scoped downloads and file selection needs no read grant.
    manifest['uses-permission'] = (manifest['uses-permission'] || []).filter(permission => permission.$['android:name'] !== 'android.permission.WRITE_EXTERNAL_STORAGE');
    manifest['uses-permission'].push({ $: { 'android:name': 'android.permission.WRITE_EXTERNAL_STORAGE', 'android:maxSdkVersion': '28', 'tools:replace': 'android:maxSdkVersion' } });
    return mod;
  });
};
