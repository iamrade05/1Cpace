# 1Cpace + Yeastar PBX integration

This release adds a Yeastar P-Series OpenAPI integration. Yeastar documents `call/dial` for click-to-call and CDR/call events for CRM integration.

## Environment

Set in `.env`:

- `PBX_BASE_URL=https://pbx.yeastarycm.co.za`
- `PBX_API_VERSION=openapi/v1.0`
- `PBX_CLIENT_ID=<Client ID from Yeastar Integrations > API>`
- `PBX_CLIENT_SECRET=<Client Secret>`
- `PBX_DEFAULT_EXTENSION=<fallback extension>`
- `PBX_DIAL_PERMISSION=<optional permitted extension>`
- `PBX_WEBHOOK_SECRET=<shared secret>`

## Yeastar setup

1. Enable the Yeastar P-Series API.
2. Create the API client and copy Client ID/Client Secret.
3. Configure a webhook pointing to `https://YOUR-1CPACE-HOST/pbx/webhook`.
4. Subscribe to call activity/CDR events.
5. Ensure the PBX API user has permission to dial the required trunks/extensions.
6. Give each 1Cpace user a `pbx_extension` value in Admin → Users. The Elev8 defaults are: Reception `1000`, Mvuleni/Manager `1002`, Sales Manager `1004`, and Sales Consultants `1001`, `1003`, `1005`, `1006` mapped to rotation positions 1–4. Empty extension fields are auto-filled on startup only when the matching staff profile is unambiguous.

## Current endpoints

- `POST /pbx/dial` — authenticated click-to-call.
- `POST /pbx/webhook` — PBX event receiver.
- `GET /pbx/calls` — authenticated call log.
- `GET /pbx/health` — authenticated API connectivity check.

The webhook secret is optional in development but should be set in production. Never put the Yeastar Client Secret in browser JavaScript.


## CRM calling workflow — v8

1. **Lead/member click-to-call** — the browser calls `POST /pbx/dial`; PBX credentials remain server-side.
2. **Collections click-to-call** — a collections task passes `collection_task_id`, so the resulting PBX call is tied to the assigned task.
3. **PBX event/CDR** — `POST /pbx/webhook` upserts the call against its external call ID.
4. **Phone matching** — inbound calls are matched to a lead/member; outbound calls match the destination number.
5. **Call outcome** — staff record an outcome, notes, next action and follow-up time through `POST /pbx/calls/<id>/outcome`.
6. **CRM timeline** — lead calls become canonical `lead_activities`; member calls become `member_notes`.
7. **Collections KPI** — PBX-linked collections calls update the existing `collection_call_tasks` state and escalation channel.

### Operational rule

The PBX records the fact that a call happened. **1Cpace records what the call means**: outcome, next action, follow-up and CRM/collections consequence. This prevents PBX CDRs from becoming a second, conflicting CRM system.

### Staff workflow

**Lead:** Lead list/profile → Call → PBX → conversation → select outcome → next action/follow-up → lead timeline.

**Member:** Member profile → Call → PBX → conversation → select outcome → member activity.

**Collections:** Call Queue → Client Profile → Call via PBX → conversation → collection outcome → existing collections escalation/KPI workflow.

### Live verification still required

The application cannot verify the actual PBX credentials, extensions, webhook delivery or trunk permissions from the development environment. Those must be tested on the Windows installation against the live Yeastar PBX.


## Elev8 PBX extension roster

| Function | Extension | 1Cpace assignment |
|---|---:|---|
| Reception | 1000 | Reception user |
| Manager | 1002 | Mvuleni |
| Sales Manager | 1004 | Sales manager |
| Sales Consultant 1 | 1001 | Sales rotation position 1 |
| Sales Consultant 2 | 1003 | Sales rotation position 2 |
| Sales Consultant 3 | 1005 | Sales rotation position 3 |
| Sales Consultant 4 | 1006 | Sales rotation position 4 |

The extension is stored on the 1Cpace user record. It is not sent to the browser as a PBX credential; it is used server-side when `/pbx/dial` selects the caller extension.
