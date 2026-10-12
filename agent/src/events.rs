use crate::protocol::{Event, State, now};
use anyhow::{Result, bail};
use sha2::{Digest, Sha256};
use zbus::{Connection, Proxy, zvariant::OwnedObjectPath};

fn emit(state: &mut State, kind: &str, count: u32) {
    if state.events.len() < 100 {
        state.events.push(Event {
            id: uuid::Uuid::new_v4().to_string(),
            kind: kind.into(),
            occurred_at: now(),
            count,
        });
    }
}

pub fn network_observation(state: &mut State, fingerprint: String) {
    if state.network_baseline.is_none() {
        state.network_baseline = Some(fingerprint);
        return;
    }
    if state.network_baseline.as_ref() == Some(&fingerprint) {
        state.network_candidate = None;
        return;
    }
    if state.network_candidate.as_ref() == Some(&fingerprint) {
        state.network_baseline = Some(fingerprint);
        state.network_candidate = None;
        if now() - state.network_alert_at >= 600 {
            emit(state, "network_change", 1);
            state.network_alert_at = now();
        }
    } else {
        state.network_candidate = Some(fingerprint);
    }
}

async fn network() -> Result<String> {
    let connection = Connection::system().await?;
    let manager = Proxy::new(
        &connection,
        "org.freedesktop.NetworkManager",
        "/org/freedesktop/NetworkManager",
        "org.freedesktop.NetworkManager",
    )
    .await?;
    let path: OwnedObjectPath = manager.get_property("PrimaryConnection").await?;
    if path.as_str() == "/" {
        bail!("No active network");
    }
    let active = Proxy::new(
        &connection,
        "org.freedesktop.NetworkManager",
        path,
        "org.freedesktop.NetworkManager.Connection.Active",
    )
    .await?;
    let id: String = active.get_property("Uuid").await?;
    Ok(hex::encode(Sha256::digest(id.as_bytes())))
}

pub async fn collect_network(state: &mut State) -> String {
    match tokio::time::timeout(std::time::Duration::from_secs(3), network()).await {
        Ok(Ok(fingerprint)) => {
            network_observation(state, fingerprint);
            "NetworkManager active-profile changes monitored".into()
        }
        _ => {
            state.network_candidate = None;
            "Unavailable: NetworkManager or active connection absent".into()
        }
    }
}

pub fn is_auth_failure(row: &serde_json::Value) -> bool {
    let message = row["MESSAGE"].as_str().unwrap_or("").to_lowercase();
    let ident = row["SYSLOG_IDENTIFIER"].as_str().unwrap_or("");
    // One canonical failure per PAM/SSH record; don't upload log text or usernames.
    (ident == "sshd" && message.starts_with("failed password for "))
        || (ident.starts_with("gdm-password")
            && message.contains("pam_unix(gdm-password:auth): authentication failure"))
        || (ident == "sudo" && message.contains("pam_unix(sudo:auth): authentication failure"))
}

pub fn authentication_observation(state: &mut State, timestamps: &[i64]) {
    let at = now();
    state.failures.extend(
        timestamps
            .iter()
            .copied()
            .filter(|t| *t <= at && *t >= at - 300),
    );
    state.failures.retain(|t| *t >= at - 300);
    if state.failures.len() >= 5 && at - state.auth_alert_at >= 600 {
        emit(state, "auth_failures", state.failures.len() as u32);
        state.auth_alert_at = at;
        state.failures.clear();
    }
}

fn journal_permission() -> bool {
    // Full system-journal visibility is required; partial user-journal visibility is insufficient.
    let mut groups = [0u32; 128];
    let count = unsafe { libc::getgroups(groups.len() as i32, groups.as_mut_ptr()) };
    if count < 0 {
        return false;
    }
    for name in [c"adm", c"systemd-journal"] {
        let group = unsafe { libc::getgrnam(name.as_ptr()) };
        if !group.is_null() && groups[..count as usize].contains(&unsafe { (*group).gr_gid }) {
            return true;
        }
    }
    false
}

pub async fn collect_auth(state: &mut State) -> String {
    if !journal_permission() {
        return "Unavailable: system-journal read permission not granted (optional adm/systemd-journal group)".into();
    }
    let mut command = tokio::process::Command::new("/usr/bin/journalctl");
    command.args([
        "--system",
        "--no-pager",
        "--quiet",
        "--output=json",
        "--show-cursor",
        "-n",
        "1000",
    ]);
    if let Some(cursor) = &state.journal_cursor {
        command.arg(format!("--after-cursor={cursor}"));
    } else {
        command.args(["--since", "5 minutes ago"]);
    }
    command.kill_on_drop(true);
    let output = tokio::time::timeout(std::time::Duration::from_secs(5), command.output()).await;
    let Ok(Ok(output)) = output else {
        return "Unavailable: journal read failed or timed out".into();
    };
    if !output.status.success() || !output.stderr.is_empty() {
        return "Unavailable: journal permission/read error".into();
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let mut timestamps = Vec::new();
    let mut rows = 0;
    for line in text.lines() {
        if let Some(cursor) = line.strip_prefix("-- cursor: ") {
            state.journal_cursor = Some(cursor.into());
            continue;
        }
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(line) {
            rows += 1;
            if let Some(cursor) = value["__CURSOR"].as_str() {
                state.journal_cursor = Some(cursor.into());
            }
            if is_auth_failure(&value) {
                if let Some(timestamp) = value["__REALTIME_TIMESTAMP"]
                    .as_str()
                    .and_then(|t| t.parse::<i64>().ok())
                {
                    timestamps.push(timestamp / 1_000_000);
                }
            }
        }
    }
    authentication_observation(state, &timestamps);
    if rows >= 1000 {
        "Limited: journal batch reached 1000 records; some events may be missed".into()
    } else {
        "SSH/GDM/sudo failures: threshold 5 in 5 minutes, cooldown 10 minutes".into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn stable_network_threshold_and_deduplication() {
        let mut state = State::default();
        network_observation(&mut state, "a".into());
        network_observation(&mut state, "b".into());
        assert!(state.events.is_empty());
        network_observation(&mut state, "b".into());
        assert_eq!(state.events.len(), 1);
        network_observation(&mut state, "b".into());
        assert_eq!(state.events.len(), 1);
    }
    #[test]
    fn auth_threshold_and_cooldown() {
        let mut state = State::default();
        authentication_observation(&mut state, &[now(); 4]);
        assert!(state.events.is_empty());
        authentication_observation(&mut state, &[now()]);
        assert_eq!(state.events.len(), 1);
        authentication_observation(&mut state, &[now(); 5]);
        assert_eq!(state.events.len(), 1);
    }
    #[test]
    fn log_rule_requires_observable_failure() {
        assert!(is_auth_failure(
            &serde_json::json!({"SYSLOG_IDENTIFIER":"sshd", "MESSAGE":"Failed password for invalid user alice"})
        ));
        assert!(!is_auth_failure(
            &serde_json::json!({"SYSLOG_IDENTIFIER":"sshd", "MESSAGE":"Accepted password for alice"})
        ));
        assert!(!is_auth_failure(
            &serde_json::json!({"SYSLOG_IDENTIFIER":"untrusted", "MESSAGE":"Failed password for alice"})
        ));
    }
}
