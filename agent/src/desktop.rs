use anyhow::{Result, bail};
use zbus::{Connection, Proxy, zvariant::OwnedObjectPath};

async fn session_locked() -> Result<bool> {
    let connection = Connection::system().await?;
    let manager = Proxy::new(
        &connection,
        "org.freedesktop.login1",
        "/org/freedesktop/login1",
        "org.freedesktop.login1.Manager",
    )
    .await?;
    let sessions: Vec<(String, u32, String, String, OwnedObjectPath)> =
        manager.call("ListSessions", &()).await?;
    let mut hints = Vec::new();
    for (_, uid, _, _, path) in sessions {
        if uid != unsafe { libc::geteuid() } {
            continue;
        }
        let session = Proxy::new(
            &connection,
            "org.freedesktop.login1",
            path,
            "org.freedesktop.login1.Session",
        )
        .await?;
        let kind: String = session.get_property("Type").await?;
        let class: String = session.get_property("Class").await?;
        if matches!(kind.as_str(), "wayland" | "x11") && class == "user" {
            hints.push(session.get_property::<bool>("LockedHint").await?);
        }
    }
    if hints.len() != 1 {
        bail!("Expected exactly one graphical session for this desktop user");
    }
    Ok(hints[0])
}

pub async fn lock() -> Result<()> {
    // systemd --user shares the user's session bus; GNOME is the narrow lock interface.
    let connection = Connection::session().await?;
    let proxy = Proxy::new(
        &connection,
        "org.gnome.ScreenSaver",
        "/org/gnome/ScreenSaver",
        "org.gnome.ScreenSaver",
    )
    .await?;
    proxy.call::<_, _, ()>("Lock", &()).await?;
    for _ in 0..20 {
        if proxy.call::<_, _, bool>("GetActive", &()).await? && session_locked().await? {
            return Ok(());
        }
        tokio::time::sleep(std::time::Duration::from_millis(250)).await;
    }
    bail!("GNOME did not confirm the screen lock");
}

pub async fn capability() -> String {
    let check = async {
        let connection = Connection::session().await?;
        let proxy = Proxy::new(
            &connection,
            "org.gnome.ScreenSaver",
            "/org/gnome/ScreenSaver",
            "org.gnome.ScreenSaver",
        )
        .await?;
        proxy.call::<_, _, bool>("GetActive", &()).await?;
        Ok::<_, anyhow::Error>(())
    };
    match tokio::time::timeout(std::time::Duration::from_secs(3), check).await {
        Ok(Ok(())) => "GNOME session bus available".into(),
        _ => "Unavailable: no reachable GNOME session; log in to the desktop".into(),
    }
}
