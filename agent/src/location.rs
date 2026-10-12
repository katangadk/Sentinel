use crate::protocol::now;

#[derive(Default)]
pub struct PositionCache {
    attempted: Option<std::time::Instant>,
    value: Option<Location>,
}

impl PositionCache {
    pub async fn sample(
        &mut self,
        client: &reqwest::Client,
        enabled: bool,
        ip_fallback: bool,
    ) -> Option<Location> {
        if !enabled && !ip_fallback {
            self.value = None;
            return None;
        }
        if self
            .attempted
            .is_none_or(|at| at.elapsed().as_secs() >= 600)
        {
            self.attempted = Some(std::time::Instant::now());
            self.value = if enabled {
                match geoclue().await {
                    Ok(value) => Some(value),
                    Err(_) if ip_fallback => ip_location(client).await.ok(),
                    Err(_) => None,
                }
            } else {
                ip_location(client).await.ok()
            };
        }
        self.value.clone()
    }
}
use anyhow::{Result, bail};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use zbus::{Connection, Proxy, zvariant::OwnedObjectPath};

pub const WIFI_INTERVAL: i64 = 600;
const NM: &str = "org.freedesktop.NetworkManager";

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct AccessPoint {
    pub bssid: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signal_dbm: Option<i16>,
}

pub fn suitable_bssid(value: &str) -> Option<String> {
    let pieces: Vec<_> = value.split(':').collect();
    if pieces.len() != 6
        || pieces
            .iter()
            .any(|v| v.len() != 2 || !v.bytes().all(|b| b.is_ascii_hexdigit()))
    {
        return None;
    }
    let bytes: Vec<u8> = pieces
        .iter()
        .map(|v| u8::from_str_radix(v, 16).unwrap())
        .collect();
    if bytes[0] & 3 != 0 || bytes.iter().all(|v| *v == 0) || bytes[..3] == [0, 0, 0x5e] {
        return None;
    }
    Some(value.to_ascii_lowercase())
}

pub fn filter_access_points(points: Vec<AccessPoint>) -> Vec<AccessPoint> {
    let mut unique = BTreeMap::new();
    for mut point in points {
        let Some(bssid) = suitable_bssid(&point.bssid) else {
            continue;
        };
        point.bssid = bssid.clone();
        if point.signal_dbm.is_some_and(|v| !(-128..=-10).contains(&v)) {
            point.signal_dbm = None
        }
        unique.entry(bssid).or_insert(point);
    }
    unique.into_values().take(20).collect()
}

fn boot_seconds() -> i64 {
    let mut value = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_BOOTTIME, &mut value) } == 0 {
        value.tv_sec
    } else {
        -1
    }
}

async fn scan_inner() -> Result<Vec<AccessPoint>> {
    let connection = Connection::system().await?;
    let manager = Proxy::new(&connection, NM, "/org/freedesktop/NetworkManager", NM).await?;
    let state: u32 = manager.get_property("State").await?;
    if state != 70 {
        bail!("Internet connection unavailable")
    }
    let devices: Vec<OwnedObjectPath> = manager.call("GetDevices", &()).await?;
    let mut found = Vec::new();
    let mut hardware = false;
    for path in devices.into_iter().take(16) {
        let device = Proxy::new(
            &connection,
            NM,
            path.clone(),
            "org.freedesktop.NetworkManager.Device",
        )
        .await?;
        if device.get_property::<u32>("DeviceType").await? != 2 {
            continue;
        }
        hardware = true;
        let wireless = Proxy::new(
            &connection,
            NM,
            path,
            "org.freedesktop.NetworkManager.Device.Wireless",
        )
        .await?;
        if wireless.get_property::<u32>("Mode").await? != 2 {
            continue;
        }
        let before: i64 = wireless.get_property("LastScan").await?;
        let options: HashMap<String, zbus::zvariant::Value<'_>> = HashMap::new();
        let scanned = wireless
            .call::<_, _, ()>("RequestScan", &(options,))
            .await
            .is_ok();
        // Permission denial permits only a recent existing scan, never root escalation.
        if scanned {
            for _ in 0..8 {
                tokio::time::sleep(std::time::Duration::from_millis(500)).await;
                if wireless.get_property::<i64>("LastScan").await? > before {
                    break;
                }
            }
        }
        let last_scan: i64 = wireless.get_property("LastScan").await?;
        if last_scan < 0 || boot_seconds() < 0 || boot_seconds() - last_scan / 1000 > 120 {
            continue;
        }
        let paths: Vec<OwnedObjectPath> = wireless.call("GetAllAccessPoints", &()).await?;
        for ap_path in paths.into_iter().take(256) {
            let ap = Proxy::new(
                &connection,
                NM,
                ap_path,
                "org.freedesktop.NetworkManager.AccessPoint",
            )
            .await?;
            let Ok(mode) = ap.get_property::<u32>("Mode").await else {
                continue;
            };
            let Ok(seen) = ap.get_property::<i32>("LastSeen").await else {
                continue;
            };
            if mode != 2 || seen < 0 || boot_seconds() - i64::from(seen) > 120 {
                continue;
            }
            let Ok(ssid) = ap.get_property::<Vec<u8>>("Ssid").await else {
                continue;
            };
            if ssid.ends_with(b"_nomap") {
                continue;
            }
            let Ok(bssid) = ap.get_property::<String>("HwAddress").await else {
                continue;
            };
            // Strength is percent, NOT dBm. Omit instead of inventing measurements.
            found.push(AccessPoint {
                bssid,
                signal_dbm: None,
            });
        }
    }
    if !hardware {
        bail!("Wi-Fi hardware unavailable")
    }
    Ok(filter_access_points(found))
}

pub async fn scan_wifi() -> Result<Vec<AccessPoint>> {
    tokio::time::timeout(std::time::Duration::from_secs(8), scan_inner()).await?
}

pub fn wifi_due(at: i64, next_attempt: i64) -> bool {
    at >= next_attempt
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Location {
    pub latitude: f64,
    pub longitude: f64,
    pub accuracy_m: Option<f64>,
    pub source: String,
    pub timestamp: i64,
}

impl Location {
    pub fn validate(&self) -> Result<()> {
        if !self.latitude.is_finite()
            || !self.longitude.is_finite()
            || !(-90.0..=90.0).contains(&self.latitude)
            || !(-180.0..=180.0).contains(&self.longitude)
            || self
                .accuracy_m
                .is_some_and(|v| !v.is_finite() || !(0.0..=20_000_000.0).contains(&v))
            || self.timestamp > now() + 30
            || self.timestamp < now() - 86400
        {
            bail!("Invalid or stale positioning result");
        }
        Ok(())
    }
}

async fn geoclue_read(connection: &Connection, path: &OwnedObjectPath) -> Result<Location> {
    let client = Proxy::new(
        connection,
        "org.freedesktop.GeoClue2",
        path.clone(),
        "org.freedesktop.GeoClue2.Client",
    )
    .await?;
    client.set_property("DesktopId", "sentinel").await?;
    client.set_property("RequestedAccuracyLevel", 6u32).await?; // city/street where provider permits it
    client.call::<_, _, ()>("Start", &()).await?;
    for _ in 0..15 {
        let location_path: OwnedObjectPath = client.get_property("Location").await?;
        if location_path.as_str() != "/" {
            let location = Proxy::new(
                connection,
                "org.freedesktop.GeoClue2",
                location_path,
                "org.freedesktop.GeoClue2.Location",
            )
            .await?;
            let timestamp: (u64, u64) = location.get_property("Timestamp").await?;
            let result = Location {
                latitude: location.get_property("Latitude").await?,
                longitude: location.get_property("Longitude").await?,
                accuracy_m: Some(location.get_property("Accuracy").await?),
                source: "geoclue".into(),
                timestamp: timestamp.0 as i64,
            };
            result.validate()?;
            return Ok(result);
        }
        tokio::time::sleep(std::time::Duration::from_secs(1)).await;
    }
    bail!("GeoClue did not provide a location");
}

pub async fn geoclue() -> Result<Location> {
    let connection = Connection::system().await?;
    let manager = Proxy::new(
        &connection,
        "org.freedesktop.GeoClue2",
        "/org/freedesktop/GeoClue2/Manager",
        "org.freedesktop.GeoClue2.Manager",
    )
    .await?;
    let path: OwnedObjectPath = manager.call("GetClient", &()).await?;
    let outcome = tokio::time::timeout(
        std::time::Duration::from_secs(20),
        geoclue_read(&connection, &path),
    )
    .await;
    // Always delete the GeoClue client, including on timeout; stops positioning.
    let _ = manager.call::<_, _, ()>("DeleteClient", &(path,)).await;
    outcome?
}

pub async fn ip_location(client: &reqwest::Client) -> Result<Location> {
    #[derive(Deserialize)]
    struct IpLocation {
        latitude: f64,
        longitude: f64,
    }
    let value = client
        .get("https://ipapi.co/json/")
        .send()
        .await?
        .error_for_status()?
        .json::<IpLocation>()
        .await?;
    let location = Location {
        latitude: value.latitude,
        longitude: value.longitude,
        accuracy_m: None,
        source: "ip_coarse".into(),
        timestamp: now(),
    };
    location.validate()?;
    Ok(location)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn wifi_filters_invalid_local_multicast_reserved_and_duplicate_addresses() {
        let values = [
            "F0:D5:BF:FD:12:AE",
            "f0:d5:bf:fd:12:ae",
            "30:86:2d:c4:29:d0",
            "02:11:22:33:44:55",
            "01:11:22:33:44:55",
            "ff:ff:ff:ff:ff:ff",
            "00:00:00:00:00:00",
            "00:00:5e:00:53:01",
            "bad",
            "ab:cc:dd:ee:ff:gg",
        ];
        let points = filter_access_points(
            values
                .into_iter()
                .map(|bssid| AccessPoint {
                    bssid: bssid.into(),
                    signal_dbm: Some(-60),
                })
                .collect(),
        );
        assert_eq!(points.len(), 2);
        assert!(points.iter().all(|p| p.signal_dbm == Some(-60)));
        assert!(points.iter().any(|p| p.bssid == "f0:d5:bf:fd:12:ae"));
    }

    #[test]
    fn wifi_percentages_are_not_rssi_and_payload_is_bounded() {
        let points = filter_access_points(vec![AccessPoint {
            bssid: "f0:d5:bf:fd:12:ae".into(),
            signal_dbm: Some(85),
        }]);
        assert!(points[0].signal_dbm.is_none());
        assert!(
            !serde_json::to_string(&points)
                .unwrap()
                .contains("signal_dbm")
        );
        let many = (1..100)
            .map(|v| AccessPoint {
                bssid: format!("f0:d5:bf:fd:12:{v:02x}"),
                signal_dbm: None,
            })
            .collect();
        assert_eq!(filter_access_points(many).len(), 20);
    }

    #[test]
    fn reconnection_and_restart_do_not_bypass_wifi_interval() {
        assert!(wifi_due(1000, 0));
        assert!(!wifi_due(1599, 1600));
        assert!(wifi_due(1600, 1600));
        let mut state = crate::protocol::State::default();
        state.wifi_next_attempt = 1600;
        let restored: crate::protocol::State =
            serde_json::from_str(&serde_json::to_string(&state).unwrap()).unwrap();
        assert!(!wifi_due(1200, restored.wifi_next_attempt));
    }

    #[tokio::test]
    #[ignore = "requires owner's real Linux NetworkManager, Wi-Fi and internet; no upload"]
    async fn linux_networkmanager_scan_without_root_or_upload() {
        assert_ne!(unsafe { libc::geteuid() }, 0);
        let points = scan_wifi().await.unwrap();
        assert!(points.len() <= 20);
        assert!(
            points
                .iter()
                .all(|p| suitable_bssid(&p.bssid).is_some() && p.signal_dbm.is_none())
        );
        println!("Suitable AP count: {}", points.len());
    }
    #[test]
    fn unavailable_and_invalid_location_not_invented() {
        let mut location = Location {
            latitude: 0.,
            longitude: 0.,
            accuracy_m: None,
            source: "geoclue".into(),
            timestamp: now(),
        };
        assert!(location.validate().is_ok()); // 0,0 can be real; never replace failed reads with it.
        location.latitude = f64::NAN;
        assert!(location.validate().is_err());
        location.latitude = 100.;
        assert!(location.validate().is_err());
    }
}
