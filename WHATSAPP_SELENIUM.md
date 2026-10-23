# Temporary Selenium WhatsApp sender

Local `.env` enables `WHATSAPP_WEB_ENABLED=1`, `WHATSAPP_WEB_BROWSER=selenium`, and `WHATSAPP_WEB_TENANT=elev8`.

Open `/admin/whatsapp-web/` while signed in to Elev8 as admin. On the gym phone open WhatsApp, Linked Devices, Link a Device, then scan the QR. The admin page shows connection status and errors. No Meta token or app secret is required for this browser connection. API webhook signature checks remain enabled.

The linked browser is shared only within the configured gym. Staff replies fall back to the browser when the Cloud API credentials are absent. Queued messages are not confirmed deliveries; the queue is in memory and is lost on restart. Incoming messages are read in WhatsApp Web, not imported into the app inbox by this fallback. No end-to-end send has been tested until a phone is linked.

Selenium stores the Chrome profile under `WHATSAPP_WEB_SESSION_DIR/selenium-profile`; keep this directory private, out of source control, and backed up separately from release code. Only one app worker can own it. Chrome/Chromium must be installed on the host; optionally set `WHATSAPP_WEB_CHROME_BINARY`. Selenium Manager resolves its driver: https://www.selenium.dev/documentation/selenium_manager/.

To return to the Cloud API, configure its credentials and disable `WHATSAPP_WEB_ENABLED`, then restart the service. Local enablement does not change production configuration.
