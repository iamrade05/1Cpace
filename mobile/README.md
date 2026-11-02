# 1Cpace for Android and iOS

Shared Expo/React Native application. Version 1.1 source opens the full staff web workspace inside Android and retains native member access through the versioned Flask API. Staff select **Open full staff workspace** and sign in using the existing website account; website permissions still apply. The 1.1 native build and phone acceptance are in progress. The currently published installer is 1.0.0.

The earlier native feature list below describes member access and the fictional staff preview. Staff web workflows are available through the workspace rather than the limited preview. Android requires an internet connection. Device push, biometric unlock and offline writes are not implemented.

## Implemented

- Native member/staff selection and distinct home/navigation.
- Staff password plus emailed one-time code. Member reference/email plus emailed code; active members only, and separately opt-in at the gym.
- Twelve-hour opaque bearer sessions stored with Expo SecureStore on devices. The server stores token hashes; logout revokes. Password/email/status changes invalidate relevant sessions, and grants are re-evaluated from current records.
- Staff: assigned leads, assigned queries, recent member directory, own pending notifications, own recent timesheet statuses, permitted class list. Lists are read-only and bounded. Directory requires the existing member permission.
- Member: own membership summary, own workout plans, class list, self-only booking/cancellation with capacity checks and retry-safe booking.
- Explicit fictional preview for both audiences. Preview changes never reach the gym.
- Loading, retry, empty/search-filter and session-expiry states. Browser preview never persists a bearer token.

## Run

Use Node 24.22 (the machine's Node 25 is outside this Expo version's declared support range).

```powershell
cd mobile
npm ci
# Optional: copy .env.example to .env and set your HTTPS server and gym code.
npm start -- --go --lan --port 8087
```

This test version targets **Expo SDK 57**, matching the user's installed Expo Go. Scan the QR with an SDK 57-compatible Expo Go installation on Android/iPhone. `npm run web` runs a browser preview; live cross-origin browser requests are not enabled by this API. Use the fictional preview in the browser, or native devices for a live server.

For the current test session, connect the phone to the same Wi-Fi as this PC and open `exp://192.168.1.110:8087` in Expo Go. The QR is at [expo-go-qr.png](../artifacts/mobile-review/expo-go-qr.png). On iPhone, scan with Camera; on Android, use Expo Go's scanner. Choose **Explore a fictional preview**, then test both staff and member modes. The server must remain running; the address may change when the PC changes networks. No Apple Developer or Expo account is needed for this local test. Physical device acceptance remains to be done.

## Backend setup

Deploy the Flask changes, including additive mobile challenge/session tables, to an HTTPS server. Configure existing email delivery and opt in:

```text
MOBILE_API_ENABLED=1
MOBILE_MEMBER_LOGIN_ENABLED=1
```

Both default to 0. Do not use production member data for testing. Confirm the member's reference and email in the existing register. This does not create new memberships or staff accounts. Every mobile login requires a delivered code even if web OTP is disabled. Code expiry is five minutes and verification is bounded to five attempts. Generic/non-platform staff password-change requirements remain enforced. Current MVP supports registered SQLite tenants; legacy PostgreSQL API requests fail closed.

Release sign-in displays the gym name and asks for account details; users do not enter server addresses. URL, tenant and gym name are configured at build time. Connection editing is available only in development builds.

Native API: `/api/mobile/v1/<gym-code>`. Tenant binding comes from the URL plus server registry, never a browser cookie. Member identity never becomes a staff login. API responses are no-store. Existing web CSRF remains enabled; the separate native blueprint accepts JSON challenge/bearer authentication, not cookies.

## Standalone Android download

The installer is published at [Download 1Cpace for Android](https://www.1cpace.co.za/static/downloads/1Cpace-Android-1.0.0.apk). Download buttons are available on the live welcome page, Elev8 sign-in page and Elev8 sidebar. The public HTTPS download was hash-verified against the signed build; the PC download server is no longer needed for normal distribution.

The signed version 1.0.0 installer is [1Cpace-Android.apk](../artifacts/apk/1Cpace-Android.apk). It supports Android 7+ on 64-bit ARM phones. Expo Go is not required. On the same Wi-Fi as this PC, open `http://192.168.1.110:8088/` while the download server is running, download the APK and open it to install. Allow installation from your browser if Android prompts. After installation the app connects directly to the live gym server over HTTPS.

Restart the local download server with `node scripts/serve-apk.cjs` from this directory. Its address can change when the PC changes networks. Signature, alignment, embedded-bundle and full-download hash checks passed; actual phone installation and sign-in remain to be verified. See the standalone installer record in `MOBILE_IMPLEMENTATION.md` for build and signing details.

## iOS

The same source builds for iPhone and iPad. On iOS, protected workspace downloads open the share sheet (Save to Files, AirDrop or another app) instead of a download manager. Files over 20 MB must be downloaded from a computer. Windows cannot compile iOS. Use EAS cloud builds (below) with an Apple Developer Program membership; EAS creates and stores the signing certificate and provisioning profile. Distribute through TestFlight or the App Store. An `internal` (ad hoc) build installs only on iPhones whose device IDs are registered first (`npx eas-cli device:create`). There is no iOS equivalent of the APK download link.

## Builds

The identifiers `com.onecpase.mobile` are project defaults; confirm ownership before signing a release. The app icon is rendered from the repository's existing 1Cpace SVG logo.

```powershell
# Local Android project, then a debug build (Android SDK and supported Java required)
npx expo prebuild --platform android
cd android
.\gradlew.bat assembleDebug

# Signed distribution through Expo EAS, after account/project setup
npx eas-cli login
npx eas-cli build:configure
npx eas-cli build --platform android --profile preview
npx eas-cli build --platform ios --profile preview
# Store artifacts
npx eas-cli build --platform all --profile production
```

Cloud builds/signing require the corresponding account/project credentials. iOS local builds require macOS/Xcode; EAS provides a cloud route from Windows. No account, signing certificate, store listing or publication is created automatically by this repository. See [Expo build setup](https://docs.expo.dev/build/setup/) and [EAS configuration](https://docs.expo.dev/build/eas-json/).

The current ignored Android project has successfully built the SDK 57 standalone installer. Earlier web/iOS exports were generated with SDK 58; regenerate those before distribution. Expo Go uses the current source independently of native projects and exports.

## Next delivery slices

1. Validate this slice against a disposable tenant and real Android/iPhone; resolve API access/rate-limit deployment and store identity.
2. Staff: lead actions/appointments, query updates, collection cases/PTP, evidence upload and controlled approvals through shared domain services.
3. HR: reviewed leave, roster and signed-week editing, preserving the existing rules/signature snapshots.
4. Member: reviewed onboarding/assessments, own documents and verified statement/payment history. Payment initiation needs an actual chosen provider; current membership fee display does not collect money.
5. Push notifications/device registration, deep links, support workflow and privacy/account-deletion requirements for store release.
6. Broader Reports/Operations/Admin workflows according to the whole-app plan. Avoid reimplementing business policy in the client.

These are not presented as complete screens. Push, payment collection, biometric sign-in, offline mutations and full web feature parity are not implemented in this slice. Class bookings follow the existing undated recurring-class model; per-occurrence bookings require a separately planned schema/service migration. Web and native booking writes use `fitness_bookings.change_booking`, with serialized SQLite writes or PostgreSQL class-row locking for shared duplicate/capacity checks.

## Verification

```powershell
# Repository root; uses isolated test databases/mail stub, never real mail
.venv\Scripts\python.exe -m pytest tests/test_mobile_api.py -q --basetemp C:/Temp/onecpase-mobile-api
cd mobile
npx tsc --noEmit
npm run export
```

Local exports run one platform/worker at a time with a bounded heap, to fit this 8 GB Windows machine. Web output is in `dist`, and native bundles in `dist-android` and `dist-ios`. Run `.venv\Scripts\python.exe scripts/verify_mobile_ui.py` from the repository root for fictional-preview and mocked-auth browser checks after exporting. This does not replace native device testing.

The implementation report in `MOBILE_IMPLEMENTATION.md` records checks actually completed and outstanding build/distribution requirements.
