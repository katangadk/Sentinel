mod desktop;
mod events;
mod location;
mod protocol;
mod storage;

use anyhow::{Context, Result, bail};
use clap::{Parser, Subcommand};
use fs2::FileExt;
use protocol::{HeartbeatReply, ResultRecord, State, now};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    io::{self, BufRead, Write},
    os::unix::fs::OpenOptionsExt,
    path::PathBuf,
    time::Duration,
};

#[derive(Parser)]
#[command(version, about = "Sentinel: least-privilege personal laptop agent")]
struct Args {
    #[arg(long, global = true)]
    config: Option<PathBuf>,
    #[command(subcommand)]
    action: Action,
}

#[derive(Subcommand)]
enum Action {
    Enroll {
        #[arg(long)]
        server: String,
        #[arg(long, default_value = "My Kali Laptop")]
        name: String,
        #[arg(long)]
        location: bool,
        #[arg(long)]
        ip_fallback: bool,
        #[arg(long)]
        development: bool,
        /// Consent to sending nearby Wi-Fi BSSIDs to the backend and Google.
        #[arg(long)]
        google_wifi: bool,
    },
    Run {
        #[arg(long)]
        once: bool,
    },
    Diagnose,
    /// Scan locally; prints counts only and never uploads Wi-Fi identifiers.
    ScanWifi,
    /// Enable/disable Google Wi-Fi sharing; see docs/GOOGLE_LOCATION.md first.
    LocationConsent {
        #[arg(
            long,
            conflicts_with = "disable_google_wifi",
            required_unless_present = "disable_google_wifi"
        )]
        google_wifi: bool,
        #[arg(long)]
        disable_google_wifi: bool,
        /// Existing backend HTTPS origin; enrollment/credentials are preserved.
        #[arg(long)]
        server: Option<String>,
    },
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Config {
    server: String,
    device_id: String,
    credential: String,
    location_enabled: bool,
    ip_fallback: bool,
    development: bool,
    #[serde(default)]
    google_wifi_enabled: bool,
}

fn valid_server(server: &str, development: bool) -> Result<String> {
    let url = reqwest::Url::parse(server)?;
    if !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || url.path() != "/"
    {
        bail!("Server must be a plain origin, without credentials or paths");
    }
    let loopback = matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    if url.scheme() != "https" && !(development && loopback && url.scheme() == "http") {
        bail!("HTTPS required; development HTTP allowed only on loopback");
    }
    Ok(server.trim_end_matches('/').to_owned())
}

fn client() -> Result<reqwest::Client> {
    Ok(reqwest::Client::builder()
        .timeout(Duration::from_secs(15))
        .connect_timeout(Duration::from_secs(5))
        .redirect(reqwest::redirect::Policy::none())
        .user_agent("Sentinel/1.0")
        .min_tls_version(reqwest::tls::Version::TLS_1_2)
        .build()?)
}

async fn acknowledge(client: &reqwest::Client, config: &Config, state: &mut State) -> Result<()> {
    for (id, record) in &mut state.commands {
        if record.acknowledged {
            continue;
        }
        let response = client
            .post(format!("{}/api/agent/commands/{id}/ack", config.server))
            .bearer_auth(&config.credential)
            .json(&serde_json::json!({"status":record.status, "detail":record.detail}))
            .send()
            .await?;
        if response.status().is_success() || matches!(response.status().as_u16(), 404 | 409) {
            record.acknowledged = true;
        } else {
            response.error_for_status()?;
        }
    }
    Ok(())
}

async fn execute_with<F, Fut>(
    command: &protocol::Command,
    config: &Config,
    state: &mut State,
    path: &std::path::Path,
    lock: F,
) -> Result<()>
where
    F: FnOnce() -> Fut,
    Fut: std::future::Future<Output = Result<()>>,
{
    // Durable consumed marker BEFORE side effects: a crash never executes the same command again.
    // The initial result intentionally reports uncertainty if execution is interrupted.
    if state.commands.contains_key(&command.id) {
        return Ok(());
    }
    command.verify(&config.device_id, &config.credential, now())?;
    state.commands.insert(
        command.id.clone(),
        ResultRecord {
            status: "failed".into(),
            detail: "execution_interrupted".into(),
            expires_at: command.expires_at,
            acknowledged: false,
        },
    );
    storage::save(path, state)?;
    let result = tokio::time::timeout(Duration::from_secs(8), lock()).await;
    let record = state.commands.get_mut(&command.id).unwrap();
    match result {
        Ok(Ok(())) => {
            record.status = "successful".into();
            record.detail = "locked_verified".into();
        }
        _ => {
            record.status = "failed".into();
            record.detail = "lock_unavailable".into();
        }
    }
    storage::save(path, state)?;
    Ok(())
}

async fn cycle(
    client: &reqwest::Client,
    config: &Config,
    state: &mut State,
    path: &std::path::Path,
    positions: &mut location::PositionCache,
) -> Result<()> {
    state.events.retain(|e| e.occurred_at > now() - 7 * 86400);
    state
        .commands
        .retain(|_, r| r.expires_at > now() - 300 || !r.acknowledged);
    let mut capabilities = BTreeMap::new();
    capabilities.insert("lock", desktop::capability().await);
    capabilities.insert("network", events::collect_network(state).await);
    capabilities.insert("authentication", events::collect_auth(state).await);
    let location = if config.google_wifi_enabled {
        None
    } else {
        positions
            .sample(client, config.location_enabled, config.ip_fallback)
            .await
    };
    let location_status = if location.is_some() {
        "available"
    } else if config.google_wifi_enabled || config.location_enabled || config.ip_fallback {
        "unavailable"
    } else {
        "disabled"
    };
    capabilities.insert("location", if config.google_wifi_enabled {
        "Google Wi-Fi positioning enabled; two suitable APs required; ten-minute minimum; no IP fallback".to_string()
    } else { match location_status {
        "available" => "Provider returned a valid approximate location",
        "disabled" => "Location collection disabled by owner configuration",
        _ => "Unavailable: check GeoClue permission, provider and network; no coordinates fabricated",
    }.to_string() });
    storage::save(path, state)?;
    // Prior results get retried before the next command fetch.
    if acknowledge(client, config, state).await.is_err() {
        eprintln!("Acknowledgements deferred; will retry");
    }
    storage::save(path, state)?;
    let reply = client.post(format!("{}/api/agent/heartbeat", config.server)).bearer_auth(&config.credential)
        .json(&serde_json::json!({"location":location,"location_status":location_status,"location_provider":if config.google_wifi_enabled { "google_wifi" } else { "legacy" },"capabilities":capabilities,"events":state.events}))
        .send().await?.error_for_status()?.json::<HeartbeatReply>().await?;
    state.last_contact = Some(now());
    state
        .events
        .retain(|event| !reply.accepted_events.contains(&event.id));
    storage::save(path, state)?;
    if (reply.server_time - now()).abs() > 30 {
        bail!("Clock differs from server by more than 30 seconds; command execution refused");
    }
    for command in &reply.commands {
        if execute_with(command, config, state, path, desktop::lock)
            .await
            .is_err()
        {
            eprintln!("Rejected invalid, unauthorized or expired command");
        }
    }
    acknowledge(client, config, state).await?;
    storage::save(path, state)?;
    // Scan after successful contact and command handling. On reconnection the
    // first eligible heartbeat retries, without bypassing the ten-minute limit.
    if config.google_wifi_enabled && location::wifi_due(now(), state.wifi_next_attempt) {
        state.wifi_next_attempt = now() + location::WIFI_INTERVAL;
        storage::save(path, state)?;
        if report_wifi(client, config, state).await.is_err() {
            eprintln!(
                "Wi-Fi positioning unavailable; monitoring continues and retries after ten minutes"
            );
        }
        storage::save(path, state)?;
    }
    Ok(())
}

async fn report_wifi(client: &reqwest::Client, config: &Config, state: &mut State) -> Result<()> {
    if !config.server.starts_with("https://") {
        bail!("Wi-Fi reports require HTTPS")
    }
    let (points, scan_error) = match location::scan_wifi().await {
        Ok(points) if points.len() >= 2 => (points, ""),
        Ok(_) => (Vec::new(), "insufficient_access_points"),
        Err(_) => (Vec::new(), "scan_unavailable"),
    };
    #[derive(Deserialize)]
    struct Reply {
        status: String,
        retry_after: i64,
    }
    let reply = client
        .post(format!("{}/api/agent/location/wifi", config.server))
        .bearer_auth(&config.credential)
        .json(&serde_json::json!({"consent":true,"scanned_at":now(),"access_points":points,"scan_error":scan_error}))
        .send()
        .await?
        .error_for_status()?
        .json::<Reply>()
        .await?;
    state.wifi_next_attempt = now() + reply.retry_after.clamp(600, 86400);
    eprintln!(
        "Google Wi-Fi position: {}",
        if reply.status == "available" {
            "received by backend"
        } else {
            "unavailable; check dashboard provider status"
        }
    );
    Ok(())
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() -> Result<()> {
    if unsafe { libc::geteuid() } == 0 {
        bail!("Run Sentinel as your desktop user, never as root");
    }
    let args = Args::parse();
    if matches!(args.action, Action::ScanWifi) {
        let points = location::scan_wifi().await?;
        println!("Suitable distinct Wi-Fi APs: {}", points.len());
        println!("Positioning eligible: {}", points.len() >= 2);
        println!(
            "RSSI omitted: NetworkManager exposes percentages, not dBm. No identifiers uploaded."
        );
        return Ok(());
    }
    let home = std::env::var_os("HOME").context("HOME is required")?;
    let path = args
        .config
        .unwrap_or_else(|| PathBuf::from(home).join(".config/sentinel/config.json"));
    let directory = path.parent().context("Config path needs a parent")?;
    storage::private_dir(directory)?;
    let lock_file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(directory.join("agent.lock"))?;
    lock_file
        .try_lock_exclusive()
        .context("Another Sentinel agent is running")?;
    let client = client()?;
    match args.action {
        Action::Enroll {
            server,
            name,
            location,
            ip_fallback,
            development,
            google_wifi,
        } => {
            if path.exists() {
                bail!("Already configured; revoke old device and remove config to enroll again");
            }
            let server = valid_server(&server, development)?;
            if google_wifi && !server.starts_with("https://") {
                bail!("Google Wi-Fi sharing requires an HTTPS backend")
            }
            eprint!("Paste one-time pairing token (input is hidden): ");
            io::stderr().flush()?;
            let stdin = io::stdin();
            let mut previous = std::mem::MaybeUninit::<libc::termios>::uninit();
            let terminal =
                unsafe { libc::tcgetattr(libc::STDIN_FILENO, previous.as_mut_ptr()) } == 0;
            let original = if terminal {
                Some(unsafe { previous.assume_init() })
            } else {
                None
            };
            if let Some(mut settings) = original {
                settings.c_lflag &= !libc::ECHO;
                unsafe {
                    libc::tcsetattr(libc::STDIN_FILENO, libc::TCSANOW, &settings);
                }
            }
            let mut token = String::new();
            let read = stdin.lock().read_line(&mut token);
            if let Some(settings) = original {
                unsafe {
                    libc::tcsetattr(libc::STDIN_FILENO, libc::TCSANOW, &settings);
                }
            }
            eprintln!();
            read?;
            #[derive(Deserialize)]
            struct Enrollment {
                device_id: String,
                credential: String,
            }
            let enrollment = client
                .post(format!("{server}/api/agent/enroll"))
                .json(&serde_json::json!({"token":token.trim(),"name":name}))
                .send()
                .await?
                .error_for_status()?
                .json::<Enrollment>()
                .await?;
            let config = Config {
                server,
                device_id: enrollment.device_id,
                credential: enrollment.credential,
                location_enabled: location,
                ip_fallback,
                development,
                google_wifi_enabled: google_wifi,
            };
            storage::save(&path, &config)?;
            println!(
                "Enrolled. Credentials saved with mode 0600. Start the user service to monitor."
            );
        }
        Action::Diagnose => {
            let config: Config = storage::load(&path)?;
            valid_server(&config.server, config.development)?;
            println!("GNOME: {}", desktop::capability().await);
            let mut state = State::default();
            println!("Network: {}", events::collect_network(&mut state).await);
            println!("Authentication: {}", events::collect_auth(&mut state).await);
            if config.google_wifi_enabled {
                println!("Google Wi-Fi: enabled; scanning locally without transmitting");
                match location::scan_wifi().await {
                    Ok(points) => println!(
                        "Suitable Wi-Fi APs: {}; RSSI omitted (NetworkManager exposes percentages)",
                        points.len()
                    ),
                    Err(_) => println!(
                        "Wi-Fi scan unavailable: check hardware, NetworkManager, permissions and internet"
                    ),
                }
            } else if config.location_enabled {
                println!(
                    "GeoClue: {}",
                    if location::geoclue().await.is_ok() {
                        "valid location acquired"
                    } else {
                        "unavailable"
                    }
                );
            } else {
                println!("GeoClue: disabled");
            }
            println!("IP fallback: {}", config.ip_fallback);
            let state_path = directory.join("state.json");
            if state_path.exists() {
                let state: State = storage::load(&state_path)?;
                println!(
                    "Last successful contact (Unix UTC): {:?}",
                    state.last_contact
                );
            }
        }
        Action::ScanWifi => {
            unreachable!("read-only scan handled before acquiring the instance lock");
        }
        Action::LocationConsent {
            google_wifi,
            disable_google_wifi: _,
            server,
        } => {
            let mut config: Config = storage::load(&path)?;
            if let Some(server) = server {
                config.server = valid_server(&server, false)?;
                config.development = false;
            }
            if google_wifi && !config.server.starts_with("https://") {
                bail!(
                    "Google Wi-Fi consent requires an HTTPS backend; pass --server https://YOUR-SENTINEL-ORIGIN"
                )
            }
            config.google_wifi_enabled = google_wifi;
            storage::save(&path, &config)?;
            println!(
                "Google Wi-Fi sharing {}. Restart the agent to apply.",
                if google_wifi {
                    "enabled with owner consent; nearby BSSIDs sent to backend and Google"
                } else {
                    "disabled"
                }
            );
        }
        Action::Run { once } => {
            let config: Config = storage::load(&path)?;
            valid_server(&config.server, config.development)?;
            let state_path = directory.join("state.json");
            let mut state = if state_path.exists() {
                storage::load::<State>(&state_path)?
            } else {
                State::default()
            };
            let mut interval = tokio::time::interval(Duration::from_secs(60));
            interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            let mut positions = location::PositionCache::default();
            loop {
                tokio::select! { _ = tokio::signal::ctrl_c() => break, _ = interval.tick() => {} }
                match cycle(&client, &config, &mut state, &state_path, &mut positions).await {
                    Ok(()) => eprintln!("Heartbeat accepted"),
                    Err(_) => {
                        eprintln!("Contact failed; results retained and retrying next interval");
                        if once {
                            bail!("One-shot contact failed");
                        }
                    }
                }
                if once {
                    break;
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn old_enrollment_never_silently_consents_to_google() {
        let config: Config = serde_json::from_value(serde_json::json!({
            "server":"https://example.com", "device_id":"test-device", "credential":"test-only", "location_enabled":true,
            "ip_fallback":false, "development":false
        })).unwrap();
        assert!(!config.google_wifi_enabled);
    }
    #[test]
    fn secure_origins_only() {
        assert!(valid_server("http://remote.example", false).is_err());
        assert!(valid_server("http://remote.example", true).is_err());
        assert!(valid_server("http://127.0.0.1:8000", true).is_ok());
        assert!(valid_server("https://example.com/path", false).is_err());
        assert!(valid_server("https://example.com", false).is_ok());
    }
    #[tokio::test]
    async fn replay_never_reexecutes_and_result_survives_restart() {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("state.json");
        let config = Config {
            server: "https://example.com".into(),
            device_id: "device".into(),
            credential: "credential".into(),
            location_enabled: false,
            ip_fallback: false,
            development: false,
            google_wifi_enabled: false,
        };
        let at = now();
        let id = uuid::Uuid::new_v4().to_string();
        use hmac::Mac;
        let mut mac = hmac::Hmac::<sha2::Sha256>::new_from_slice(b"credential").unwrap();
        mac.update(format!("{id}\ndevice\nlock\n{at}\n{}", at + 120).as_bytes());
        let command = protocol::Command {
            id: id.clone(),
            device_id: "device".into(),
            action: "lock".into(),
            issued_at: at,
            expires_at: at + 120,
            signature: hex::encode(mac.finalize().into_bytes()),
        };
        let mut state = State::default();
        execute_with(&command, &config, &mut state, &path, || async { Ok(()) })
            .await
            .unwrap();
        let mut reloaded: State = storage::load(&path).unwrap();
        execute_with(&command, &config, &mut reloaded, &path, || async {
            panic!("replayed lock executed")
        })
        .await
        .unwrap();
        assert_eq!(reloaded.commands[&id].status, "successful");
    }
}
