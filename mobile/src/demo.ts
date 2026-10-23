import type { Home, Item, Kind } from './api';
// Explicit fictional preview; no production requests or writes.
export function demoHome(kind: Kind): Home {
  return kind === 'member' ? { kind, name: 'Alex', gym: '1Cpace Demo Gym', reference: 'DEMO-001', membership: 'Active', package: 'Premium membership', monthly_fee: 599, access: 'allowed', features: ['classes','plans','account'] }
    : { kind, name: 'Taylor', gym: '1Cpace Demo Gym', department: 'Reception', role: 'staff', features: ['leads','members','queries','classes','notifications','hr'] };
}
export const demoItems: Record<string, Item[]> = {
  classes: [{ id: 1, title: 'Strength foundations', subtitle: 'Monday · 17:30', status: '8 places', booked: false },
    { id: 2, title: 'Morning mobility', subtitle: 'Tuesday · 06:30', status: 'Booked', booked: true },
    { id: 3, title: 'Full body circuit', subtitle: 'Wednesday · 18:00', status: '5 places', booked: false }],
  plans: [{ id: 1, title: 'Build your foundation', subtitle: 'Your current programme', status: 'Active', detail: 'Three sessions per week. Warm up, practise your main movements, and record how you feel. Ask your trainer to review your progress.' }],
  account: [{ id: 1, title: 'Premium membership', subtitle: 'DEMO-001', status: 'Active', detail: 'Monthly membership: R599.00. Fictional preview information.' }],
  leads: [{ id: 1, title: 'Sam Example', subtitle: 'Website enquiry', status: 'Assigned', detail: 'Make first contact and arrange a visit.' }, { id: 2, title: 'Jordan Example', subtitle: 'Walk-in', status: 'Follow-up', detail: 'Follow up after consultation.' }],
  members: [{ id: 1, title: 'Alex Example', subtitle: 'DEMO-001', status: 'Active', detail: 'Premium membership' }],
  queries: [{ id: 1, title: 'DEMO-Q001', subtitle: 'Membership', status: 'Open', detail: 'Member requested information about their membership.' }],
  notifications: [{ id: 1, title: 'A follow-up needs your attention', subtitle: 'Sales', status: 'Due today' }],
  hr: [{ id: 1, title: 'Week of 2026-10-05', subtitle: 'My signed time record', status: 'Approved' }],
};
