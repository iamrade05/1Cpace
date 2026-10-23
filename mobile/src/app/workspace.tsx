import { router } from 'expo-router';
import StaffWorkspace from '../StaffWorkspace';

export default function Workspace() {
  return <StaffWorkspace onClose={() => { if (router.canGoBack()) router.back(); else router.replace('/'); }} />;
}
