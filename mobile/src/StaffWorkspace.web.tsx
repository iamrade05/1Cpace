import React from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';
import { workspaceOrigin } from './workspacePolicy';

export default function StaffWorkspace({ onClose }: { onClose: () => void }) {
  const origin = workspaceOrigin(process.env.EXPO_PUBLIC_API_URL || 'https://www.1cpace.co.za');
  return <View style={styles.page}>
    <Text style={styles.title}>Full staff workspace</Text>
    <Text>The Android app opens the complete gym system inside the app. In this browser preview, open the website to continue.</Text>
    <Pressable accessibilityRole="link" onPress={() => Linking.openURL(origin)} style={styles.button}><Text style={styles.label}>Open staff website</Text></Pressable>
    <Pressable accessibilityRole="button" onPress={onClose} style={styles.button}><Text style={styles.label}>Return to member access</Text></Pressable>
  </View>;
}
const styles = StyleSheet.create({ page: { flex: 1, padding: 28, justifyContent: 'center', gap: 20, backgroundColor: '#f6f7f2' }, title: { fontSize: 26, fontWeight: '700' }, button: { padding: 16, borderRadius: 10, backgroundColor: '#15392f' }, label: { color: '#fff', fontWeight: '700' } });
