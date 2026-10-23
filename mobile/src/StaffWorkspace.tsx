import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, BackHandler, Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { printAsync } from 'expo-print';
import { WebView } from 'react-native-webview';
import { navigationAction, readPrintMessage, workspaceOrigin } from './workspacePolicy';
import { workspaceBridge } from './workspaceBridge';

export default function StaffWorkspace({ onClose }: { onClose: () => void }) {
  const web = useRef<WebView>(null);
  const canGoBack = useRef(false);
  const printing = useRef(false);
  const [origin] = useState(() => workspaceOrigin(process.env.EXPO_PUBLIC_API_URL || 'https://www.1cpace.co.za'));
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState('');
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
      if (canGoBack.current && !failed) web.current?.goBack(); else onClose();
      return true;
    });
    return () => subscription.remove();
  }, [failed, onClose]);

  function home() {
    // A fresh GET avoids replaying a failed form POST.
    setFailed(false); setNotice(''); setLoading(true); canGoBack.current = false;
    setGeneration(value => value + 1);
  }

  function external(url: string) {
    Linking.openURL(url).catch(() => setNotice('No app could open this link.'));
  }

  return <SafeAreaView style={styles.screen} edges={['top', 'bottom']}>
    <StatusBar style="dark" />
    <View style={styles.toolbar}>
      <Pressable accessibilityRole="button" accessibilityLabel="Go back" style={styles.control} onPress={() => {
        if (canGoBack.current && !failed) web.current?.goBack(); else onClose();
      }}><Text style={styles.controlText}>‹ Back</Text></Pressable>
      <Text style={styles.title}>1Cpace workspace</Text>
      <Pressable accessibilityRole="button" accessibilityLabel="Workspace home" style={styles.control} onPress={home}><Text style={styles.controlText}>Home</Text></Pressable>
    </View>
    {!!notice && <View style={styles.notice}><Text accessibilityRole="alert" style={styles.body}>{notice}</Text><Pressable accessibilityRole="button" accessibilityLabel="Dismiss message" onPress={() => setNotice('')}><Text style={styles.controlText}>Dismiss</Text></Pressable></View>}
    <View style={styles.browser}>
      <WebView key={generation} ref={web} source={{ uri: origin + '/' }}
        style={styles.browser} javaScriptEnabled domStorageEnabled sharedCookiesEnabled
        thirdPartyCookiesEnabled={false} mixedContentMode="never"
        allowFileAccess={false} allowFileAccessFromFileURLs={false} allowUniversalAccessFromFileURLs={false}
        cacheEnabled={false} cacheMode="LOAD_NO_CACHE" setSupportMultipleWindows
        javaScriptCanOpenWindowsAutomatically={false} allowsBackForwardNavigationGestures
        originWhitelist={['*']} injectedJavaScript={workspaceBridge}
        onShouldStartLoadWithRequest={request => {
          const action = navigationAction(request.url, origin);
          if (action === 'internal') return true;
          if (action === 'external') external(request.url);
          else setNotice('This link cannot be opened in the workspace.');
          return false;
        }}
        onOpenWindow={({ nativeEvent }) => {
          const action = navigationAction(nativeEvent.targetUrl, origin);
          if (action === 'internal') web.current?.injectJavaScript('window.location.assign(' + JSON.stringify(nativeEvent.targetUrl) + ');true;');
          else if (action === 'external') external(nativeEvent.targetUrl);
          else setNotice('This link cannot be opened in the workspace.');
        }}
        onNavigationStateChange={state => { canGoBack.current = state.canGoBack; }}
        onLoadStart={() => { setLoading(true); setFailed(false); }}
        onLoadEnd={() => setLoading(false)}
        onError={() => { setLoading(false); setFailed(true); }}
        onRenderProcessGone={() => { setLoading(false); setFailed(true); }}
        onContentProcessDidTerminate={() => { setLoading(false); setFailed(true); }}
        onMessage={async ({ nativeEvent }) => {
          const printable = readPrintMessage(nativeEvent.data, nativeEvent.url, origin);
          if (!printable || printing.current) {
            if (!printable && navigationAction(nativeEvent.url, origin) === 'internal') setNotice('This page could not be prepared for printing.');
            return;
          }
          printing.current = true;
          try { await printAsync({ html: printable.html }); }
          catch { setNotice('Printing did not complete. Try Print / Save PDF again.'); }
          finally { printing.current = false; }
        }}
        renderError={() => <View style={styles.browser} />}
      />
      {loading && !failed && <View pointerEvents="none" style={styles.loading}><ActivityIndicator color="#15392f" size="small" /><Text style={styles.body}>Loading workspace…</Text></View>}
      {failed && <View style={styles.error}>
        <Text style={styles.errorTitle}>Couldn’t load the workspace</Text>
        <Text style={styles.body}>Check your internet connection, then return to the workspace. If you were saving a change, check its status before trying again.</Text>
        <Pressable accessibilityRole="button" style={styles.button} onPress={home}><Text style={styles.buttonText}>Return to workspace</Text></Pressable>
        <Pressable accessibilityRole="button" style={styles.control} onPress={onClose}><Text style={styles.controlText}>Return to member access</Text></Pressable>
      </View>}
    </View>
  </SafeAreaView>;
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#fff' }, browser: { flex: 1 },
  toolbar: { minHeight: 50, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 1, borderBottomColor: '#dfe5da', paddingHorizontal: 6 },
  title: { color: '#152e2b', fontWeight: '700', fontSize: 14, flexShrink: 1 },
  control: { padding: 12, minHeight: 44, justifyContent: 'center' }, controlText: { color: '#15392f', fontWeight: '600' },
  notice: { padding: 12, backgroundColor: '#f5f7ee', flexDirection: 'row', alignItems: 'center', gap: 6 },
  body: { color: '#52625b', fontSize: 13, lineHeight: 20, flexShrink: 1 },
  loading: { position: 'absolute', top: 0, left: 0, right: 0, padding: 10, flexDirection: 'row', justifyContent: 'center', gap: 10, backgroundColor: '#f5f7ee' },
  error: { position: 'absolute', top: 0, bottom: 0, left: 0, right: 0, backgroundColor: '#fff', padding: 28, justifyContent: 'center', gap: 18 },
  errorTitle: { fontWeight: '700', fontSize: 23, color: '#152e2b' },
  button: { backgroundColor: '#15392f', padding: 16, borderRadius: 10, alignItems: 'center' }, buttonText: { color: '#fff', fontWeight: '700' },
});
