use anyhow::{Result, bail};
use hmac::{Hmac, Mac};
use serde::{Deserialize, Serialize};
use sha2::Sha256;
use std::collections::BTreeMap;

pub fn now() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs() as i64
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Command {
    pub id: String,
    pub device_id: String,
    pub action: String,
    pub issued_at: i64,
    pub expires_at: i64,
    pub signature: String,
}

impl Command {
    pub fn verify(&self, device: &str, secret: &str, at: i64) -> Result<()> {
        uuid::Uuid::parse_str(&self.id)?;
        if self.device_id != device
            || self.action != "lock"
            || self.expires_at <= at
            || self.issued_at > at + 30
            || self.issued_at < at - 150
            || self.expires_at <= self.issued_at
            || self.expires_at - self.issued_at > 120
        {
            bail!("Unauthorized, expired or invalid command");
        }
        let canonical = format!(
            "{}\n{}\nlock\n{}\n{}",
            self.id, self.device_id, self.issued_at, self.expires_at
        );
        let mut mac = Hmac::<Sha256>::new_from_slice(secret.as_bytes())?;
        mac.update(canonical.as_bytes());
        mac.verify_slice(&hex::decode(&self.signature)?)?;
        Ok(())
    }
}

#[derive(Serialize, Deserialize, Clone)]
pub struct Event {
    pub id: String,
    pub kind: String,
    pub occurred_at: i64,
    pub count: u32,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct ResultRecord {
    pub status: String,
    pub detail: String,
    pub expires_at: i64,
    pub acknowledged: bool,
}

#[derive(Serialize, Deserialize, Default)]
#[serde(default)]
pub struct State {
    pub commands: BTreeMap<String, ResultRecord>,
    pub events: Vec<Event>,
    pub last_contact: Option<i64>,
    pub journal_cursor: Option<String>,
    pub failures: Vec<i64>,
    pub network_baseline: Option<String>,
    pub network_candidate: Option<String>,
    pub auth_alert_at: i64,
    pub network_alert_at: i64,
    pub wifi_next_attempt: i64,
}

#[derive(Deserialize)]
pub struct HeartbeatReply {
    pub server_time: i64,
    pub commands: Vec<Command>,
    pub accepted_events: Vec<String>,
}

#[cfg(test)]
mod tests {
    use super::*;
    fn command() -> Command {
        let mut command = Command {
            id: "550e8400-e29b-41d4-a716-446655440000".into(),
            device_id: "device".into(),
            action: "lock".into(),
            issued_at: 1000,
            expires_at: 1120,
            signature: String::new(),
        };
        let mut mac = Hmac::<Sha256>::new_from_slice(b"credential").unwrap();
        mac.update(b"550e8400-e29b-41d4-a716-446655440000\ndevice\nlock\n1000\n1120");
        command.signature = hex::encode(mac.finalize().into_bytes());
        command
    }
    #[test]
    fn valid_signed_command() {
        assert!(command().verify("device", "credential", 1050).is_ok());
    }
    #[test]
    fn expiry_owner_secret_action_and_tampering_rejected() {
        let mut c = command();
        assert!(c.verify("device", "credential", 1120).is_err());
        assert!(c.verify("other", "credential", 1050).is_err());
        assert!(c.verify("device", "wrong", 1050).is_err());
        c.action = "shell".into();
        assert!(c.verify("device", "credential", 1050).is_err());
        c.action = "lock".into();
        c.expires_at += 1;
        assert!(c.verify("device", "credential", 1050).is_err());
    }
}
