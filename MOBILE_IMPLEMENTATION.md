# Android and iOS implementation

User-selected scope: both staff and members, with separate experiences. Server: `https://www.1cpace.co.za`. Shared client: `mobile/` (Expo/React Native/TypeScript). Backend: `onecpase/mobile_api.py`, opt-in versioned native API.

## iOS preparation — 29 October 2026

The user asked for an iOS version. The shared Expo source (native member access plus the full staff workspace) now carries iOS-specific behaviour and configuration. No backend, business-rule or database change.

- **Downloads:** WKWebView has no system download manager. `onFileDownload` (attachments and non-renderable files) asks the current page to fetch the file with its own session, since every website download route is a GET. The app then validates the message (`readDownloadMessage`: trusted page, same-origin file, base64 only, sanitised file name), writes it to the cache and opens the iOS share sheet (Save to Files, AirDrop, other apps). It deletes the cached copy afterwards. Files over 20 MB, such as long screen recordings, show a message to use a computer instead.
- **Uploads:** camera, photo-library and microphone usage descriptions are set, because WKWebView file inputs offer those sources.
- **App Transport Security:** HTTPS only (`NSAllowsArbitraryLoads` false, local networking allowed for development). `ITSAppUsesNonExemptEncryption` is false because only standard HTTPS is used.
- **Other:** Android installer links show a notice on iPhone instead of opening Safari. Print / Save PDF uses the existing `expo-print` bridge (AirPrint). Back uses the toolbar button and the WebView swipe gesture. Bundle ID `com.onecpase.mobile`, version 1.1.0, build 1. New dependencies: `expo-file-system` and `expo-sharing` (share-into extension not enabled).

Validation: five workspace policy tests (including new download checks), TypeScript, Expo lint, resolved iOS config (Info.plist keys present) and a production iOS Hermes export (`dist-ios`) all passed. `expo install --check` reports existing patch updates for `expo` (57.0.31) and `expo-router` (57.0.29); these were not changed. Expo does not generate the iOS native project on Windows, so no Xcode project, IPA or device test exists yet. Building requires EAS cloud with an Expo account and a paid Apple Developer Program membership, or a Mac with Xcode. Neither has been set up by this task.

## Full staff workspace Android update ? 18 October 2026

The user selected the existing full staff web workspace inside the standalone Android app, with native member access retained. Version 1.1.0 / code 2 source implements Expo Router navigation and an HTTPS WebView with normal website authentication, CSRF and role/phase permissions. Staff use **Open full staff workspace**; the limited native staff preview is fictional.

Protected downloads use Android DownloadManager with website cookies. A print bridge preserves current form values, signatures and protected images while excluding passwords and scripts, then opens Android Print / Save PDF. External links open appropriate apps. Staff screen recordings can be uploaded from a phone file chooser through the existing protected upload endpoint. No business rules or database schema change for this update.

Validation completed: TypeScript, Expo lint, SDK dependency check, four workspace policy tests, 15 existing authentication/tenant/recording tests, browser print/upload fixtures, and 360/390 px fictional-preview/mocked-member-auth journeys. Native release build completed (Gradle BUILD SUCCESSFUL in 2h 12m 46s; log `artifacts/workspace-android-build.log`). Signed with the same 1.0 key as `artifacts/apk/1Cpace-Android-1.1.0.apk` (40,458,610 bytes, SHA-256 `d1f6cc40a4c763fadc6d2d8f0f6597daf24e2665daf6d6edd949bcd129b439d3`): version 1.1.0 / code 2, API 24–36, arm64-v8a only; v2/v3 signatures and 16 KB alignment verified (`artifacts/android-apk-1.1.0-signing.log`). The 1.0 file is preserved as `artifacts/apk/1Cpace-Android-1.0.0.apk`; the LAN download server now serves 1.1 (full download hash matched). Installed by adb as an in-place update over 1.0 on a Samsung SM-A055F (Android 15); the app launched with no crash. On-device acceptance (read-only): an Admin staff account was signed into the full workspace on the phone; the live dashboard loaded; the session survived closing and reopening the app (dashboard took roughly 10–25 s to appear on reopen); the sidebar menu opened; Sales CRM opened; and the Android Back key returned to the previous workspace page, then to the app's start screen, then exited — expected behaviour. Downloads, Print/Save PDF and recording upload were not exercised on the phone, and no records were changed. Website issue seen in the app (not app-specific): the dashboard "Synced" time shows server UTC (two hours behind SAST) because `onecpase/dashboard.py` uses a naive `datetime.now()`. The public website download is still version 1.0; 1.1 has not been published there.

Historical records below describe earlier delivery stages and their feature boundaries.

## Standalone Android installer — 18 October 2026

The installer is now published at `https://www.1cpace.co.za/static/downloads/1Cpace-Android-1.0.0.apk`, with download links on the welcome page, Elev8 sign-in page and Elev8 sidebar. A selective four-file publication preserved live template changes and backed up the three replaced templates under `/opt/onecpase-backups/android-download-20261018T142910Z/before`. No database or environment changes were needed. Public welcome/sign-in/health returned 200, and a full HTTPS download matched the signed APK hash. Phone-sized browser checks at 390 px confirmed visible download links and no horizontal overflow; screenshots and publication records are in `artifacts/apk-publish/`. Existing authentication/navigation regression checks: 4 passed. The published static APK is available independently of the development PC. Actual phone installation/sign-in remains unverified.

The user selected a standalone Android APK after Expo Go testing. Gradle finished successfully in 27m 12s (299 tasks). The release APK is built and signed at `artifacts/apk/1Cpace-Android.apk` (26,381,407 bytes), version 1.0.0 / code 1, package `com.onecpase.mobile`. It supports Android 7+ (API 24), targets API 36 and includes arm64-v8a native libraries only. It embeds the production Hermes bundle and the `https://www.1cpace.co.za` connection; Expo Go and a Metro server are not required.

APK v2/v3 signature verification and 16 KB ZIP alignment checks passed. The certificate is `CN=1Cpace Mobile, O=1Cpace, C=ZA`, SHA-256 `d2cc09d53dac51a480e9de043f16258e672b387b12af12907bb656cb647a2e20`. The signing key and Windows-protected password remain outside the repository under the current user's `.android/onecpase-mobile` directory. Preserve those files for updates; the protected password is tied to this Windows account. Signing credentials are not included in the APK download server.

APK SHA-256: `30676c09023878e76fd792917cd1099c9c4d9e4e401787bb115b7ae34bf9943b`. A complete LAN download returned HTTP 200 and matched the file hash. The download page is `http://192.168.1.110:8088/` while `mobile/scripts/serve-apk.cjs` is running on this PC; the phone must use the same Wi-Fi for the download. After installation, gym access uses the internet directly. This is a sideloaded installer, not a Play Store publication. Physical installation, sign-in and email-code acceptance remain unverified because no Android device was connected.

The build required NDK 27.1.12297006, CMake 3.22.1 and Build Tools 35/36. A stalled automatic NDK download was replaced by `android-cli.exe sdk install ndk/27.1.12297006`. Java 17 plus IPv4 preference and 15-second Gradle HTTP timeouts allowed dependency downloads to progress. Node 24.19 from the local runtime was used. The build used one Gradle worker, a 1536 MB Java heap and arm64 only. Logs: `artifacts/android-build-network.log`, `artifacts/android-apk-signing.log`, `artifacts/android-apk-package.txt`; content and download checks are under `artifacts/apk/`.

The signing script now handles normal native-tool stderr in Windows PowerShell without treating it as a failed command, while still checking exit codes. Earlier Expo Go-only and native packaging-deferred notes below are historical and are superseded by this installer record.

## Live API deployment — 18 October 2026

The backup count/free-space figures in this deployment record preceded the user's subsequent five-per-type retention policy. See `BACKUP_RETENTION.md` and `artifacts/backup-retention/result.json` for the current policy and cleanup result.

The user approved deployment after the phone reached sign-in but the live API returned 404. A selective seven-file release was generated from current server source, preserving unrelated changes. The shared `onecpase` service serves both the primary and beta domains. Both mobile flags are now enabled. The existing release phase, mail settings, secrets and business records were preserved.

The actual staged server source passed **54 tests**, covering mobile authentication/tenant boundaries, shared booking rules, web authentication, phase gates, tenant isolation, startup, platform permissions and production headers. Test tools and databases were isolated from the live runtime and data. A broad local suite was started and stopped in favor of this staged regression gate; no full-suite pass is claimed for this deployment.

Before rollout, the server disk was full. Rebuildable package caches were cleared, and three older database backups were compressed with decompressed SHA-256 equality checks. The rollout then backed up all seven affected source files, the existing environment and two databases, hash-checked the live source, installed the changes and restarted the service. The two new database backups were also compressed and verified. There are now **42 database backup files and 14 archive files** across `/opt/onecpase-backups` and `/opt/onecpase/backups`, with approximately **2.17 GB free** after rollout. Backup contents were preserved; compressed copies require decompression for restoration.

Public verification passed: primary website and health return 200, beta health returns 200, the Elev8 mobile configuration returns 200 with staff/member enabled and `Cache-Control: no-store`, and unauthenticated mobile home returns 401. No real login, OTP email or class booking was performed. On-device sign-in and actual email delivery remain for the user to verify.

Rollback source, settings and compressed database snapshots are stored under `/opt/onecpase-backups/mobile-api-20261018/before`. The deployment script restores prior code/settings and restarts on failure; additive tables may remain. Audit records, selective patch, staged test results, backup checksums and public checks are in `artifacts/mobile-release/`. Historical pre-deployment statements below describe earlier validation stages.

## Expo Go SDK compatibility correction — 18 October 2026

The phone reported an incompatible SDK and the user confirmed Expo Go supports SDK 57. The client now targets Expo ~57.0.30, React Native 0.86.3, React/React DOM 19.2.3, SecureStore ~57.0.4 and StatusBar ~57.0.1, with SDK-compatible safe-area and React type packages resolved by `expo install --fix`. The lockfile is updated. ESLint is configured using Expo's SDK 57 preset.

TypeScript, lint and `expo install --check` passed. Metro was restarted with its cache cleared at the same LAN address, `192.168.1.110:8087`. Both manifests advertise `exposdk:57.0.0`; both Android and iOS development bundles compiled and returned HTTP 200. The user must fully close Expo Go and scan again, then confirm actual device behavior. The earlier production exports and browser checks below were performed with SDK 58 and are historical; regenerate them before distribution. No backend changes were needed for this correction.

## Current feature boundary

| Audience | Implemented | Remaining |
| --- | --- | --- |
| Both | Separate navigation, tenant-aware sign-in, emailed code, opaque expiring sessions, logout endpoint, refresh/error/empty states, explicit fictional preview | Device push, deep links, offline operation, biometric unlock and device-level acceptance |
| Staff | Password+code, permission/phase-aware capabilities, assigned lead/query lists, permitted recent member directory, own notification list, own weekly statuses, permitted class list | Lead actions/appointments, case edits, collection/PTP, approvals, documents, signed-week editing, leave/roster, Reports/Operations/Admin parity |
| Member | Reference+email+code against active member record, own membership summary, own workout plans, class availability, own booking/cancellation | Verified financial statements, payment initiation/provider checkout, onboarding/assessment display, documents and support workflow |
| Platforms | Android/iOS source and build configuration; existing repository logo rendered as app icon | Signed distribution artifacts, device testing, final store registration/signing configuration |

Native classes and website bookings now call one transaction service in `fitness_bookings.py`. It checks actual member/class identity, duplicate bookings and capacity under SQLite write serialization or a PostgreSQL class-row lock. Classes retain the existing recurring/undated booking model; per-occurrence scheduling needs a separate schema/workflow migration.

## Identity and access

- Member tokens do not authenticate any staff endpoint or browser session.
- Tenant comes from the path and active server registry. Token/challenge hashes are tenant-scoped, including when tenant data is copied.
- Server stores token hashes and OTP challenge hashes; devices store bearer credentials using SecureStore. Browser preview is memory-only.
- Five-minute OTP, five verification attempts, consumed challenge and 12-hour session; request rate limits apply through the existing limiter.
- Active state and credential/email fingerprint are rechecked, and staff permissions are recomputed on each protected data request.
- Mobile requests do not accept browser-cookie authentication. CSRF exemption is limited to the native blueprint with JSON challenge/bearer authentication; existing web forms retain CSRF protection.
- Member and API access are separately off by default. Current API supports registered SQLite tenants, not legacy PostgreSQL mode. The shared booking service supports both database backends for its web callers.

## Public connection check

Read-only checks on 18 October 2026 returned HTTP 200 for the site's root and `/healthz`; `/api/mobile/v1/elev8/configuration` returned 404. The local native backend has not been deployed/enabled there. No live member/account request or mutation was performed.

Deployment must include the additive mobile schema, API registration and configuration, shared booking service, and email delivery. Set `MOBILE_API_ENABLED=1` and, when ready for active-member sign-in, `MOBILE_MEMBER_LOGIN_ENABLED=1`. Existing records need correct member reference/email. No bulk account creation is performed.

## Validation record

- Initial native API suite: 13 passed; expanded tenant/expiry suite: 14 passed.
- Final mobile suite, including shared web/native booking, member-mail failure privacy and secret-rotation revocation: **17 passed**.
- Existing auth, phase, tenant isolation, platform Power User and startup connection regression: **28 passed**.
- Clean dependency installation from lockfile succeeded; final TypeScript check passed.
- Web, Android and iOS production JavaScript/Hermes exports passed sequentially with one worker. Outputs: `mobile/dist`, `mobile/dist-android`, `mobile/dist-ios`.
- Browser journeys passed at 360 and 390 px: member/staff previews, simulated booking, filtering, mocked sign-in and expired-session clearing. No page errors or horizontal overflow. Results/screenshots: `artifacts/mobile-review/`.
- Android packaging was stopped after the user selected Expo Go testing first. No APK/IPA or on-device acceptance is claimed. The Expo Go server serves valid Android and iOS manifests at `192.168.1.110:8087`; both development bundles also returned HTTP 200 after compilation. QR and checks are in `artifacts/mobile-review/`.
- Public endpoint status checks as above.

See [mobile/README.md](mobile/README.md) for local run/build commands and remaining delivery slices. iOS local compilation needs macOS/Xcode; signed cloud builds need an EAS project and platform signing credentials. No signing credentials or store publication have been created.

## Local build constraints

The machine has 8 GB RAM. Running native compilation and multi-platform export together caused memory pressure; those attempts were stopped. The maintained export command runs platforms sequentially with a 512 MB heap and one worker. Expo Go testing is the current delivery target, as requested; signed builds and store submission are deferred.

Production is a separate target from the repository's beta deploy script. The beta release process requires the full pytest suite and replaces the application package; no beta or production deployment was performed by this mobile implementation task.
