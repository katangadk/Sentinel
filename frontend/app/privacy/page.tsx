export default function Privacy() {
  return <main className="shell"><section className="card"><h1>Sentinel privacy</h1>
    <p>Sentinel is a personal laptop security and recovery dashboard. Location is visible only to the enrolled laptop’s authenticated owner.</p>
    <p>Google Wi-Fi positioning requires separate consent on the laptop. When enabled, the agent reads nearby access-point BSSIDs and sends suitable identifiers to your Sentinel backend over HTTPS. The backend sends them to Google Maps Geolocation. Network names and Wi-Fi passwords are never uploaded. Signal strength is included only when a genuine dBm measurement is available.</p>
    <p>Sentinel keeps Wi-Fi identifiers only in memory during each request, without logs, a history or a retry queue. Google handles this data under its <a href="https://policies.google.com/privacy">Privacy Policy</a>. Google controls its own retention.</p>
    <p>The backend retains only the latest Google coordinates for up to 24 hours, replacing older positions. Accuracy is not retained by default because Google’s coordinate caching exception does not explicitly cover it. Separate permission from Google is required before enabling accuracy retention. No location history is built. Disable Google Wi-Fi sharing on the laptop or revoke the device to stop Google Wi-Fi reports.</p>
    <p>Sessions and credentials are protected; security alerts and audit records expire after 30 days. Your hosting provider handles server traffic. If using the temporary Cloudflare link, Cloudflare terminates HTTPS and handles sign-in and device traffic. Legacy maps, if you choose to load them, send tile requests to OpenStreetMap; Google positions are never plotted on that map.</p>
    <p><a href="/">Back to Sentinel</a> · <a href="/terms">Terms</a></p>
  </section></main>;
}
