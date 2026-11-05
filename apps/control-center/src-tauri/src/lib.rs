use serde::{Deserialize, Serialize};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use tauri::Manager;

const PERM_MGR_SOCKET: &str = "/run/tupi-perm-mgr/perm-mgr.sock";

#[derive(Serialize, Deserialize, Clone)]
pub struct PermissionRequest {
    pub agent_id: String,
    pub operation: String,
    pub target: String,
    pub request_id: String,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct PermissionResponse {
    pub decision: String,
    pub category: String,
    pub reason: String,
    pub request_id: String,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct CommitInfo {
    pub id: String,
    pub message: String,
    pub timestamp: String,
    pub author: String,
    pub status: String, // "pending", "approved", "applied", "rejected"
}

#[tauri::command]
fn check_permission(agent_id: String, operation: String, target: String) -> Result<PermissionResponse, String> {
    let request_id = format!("{}", chrono::Utc::now().timestamp_nanos());
    let req = PermissionRequest {
        agent_id,
        operation,
        target,
        request_id: request_id.clone(),
    };

    let json = serde_json::to_string(&req).map_err(|e| e.to_string())?;

    let mut stream = UnixStream::connect(PERM_MGR_SOCKET)
        .map_err(|e| format!("Cannot connect to Permission Manager: {}", e))?;

    stream.write_all(json.as_bytes()).map_err(|e| e.to_string())?;
    stream.flush().map_err(|e| e.to_string())?;

    let mut response = String::new();
    stream.read_to_string(&mut response).map_err(|e| e.to_string())?;

    serde_json::from_str(&response).map_err(|e| format!("Invalid response: {}", e))
}

#[tauri::command]
fn list_commits() -> Result<Vec<CommitInfo>, String> {
    // Read from ST pending manifests and PD git log
    // For Phase 6 MVP: return placeholder structure
    Ok(vec![
        CommitInfo {
            id: "st-snapshot-20261106".to_string(),
            message: "AI-generated config update (Sway bar layout)".to_string(),
            timestamp: "2026-11-06T17:00:00Z".to_string(),
            author: "code-assistant".to_string(),
            status: "pending".to_string(),
        },
    ])
}

#[tauri::command]
fn approve_commit(commit_id: String) -> Result<String, String> {
    // Validate through Permission Manager before approving
    let perm = check_permission(
        "control-center".to_string(),
        "write_system_config".to_string(),
        format!("/var/lib/tupi-st/{}", commit_id),
    )?;

    if perm.decision == "allow" || perm.decision == "pending" {
        // In full implementation: promote ST snapshot to PD via btrfs subvolume swap
        Ok(format!("Commit {} approved (decision: {})", commit_id, perm.decision))
    } else {
        Err(format!("Commit rejected: {}", perm.reason))
    }
}

#[tauri::command]
fn get_system_status() -> Result<serde_json::Value, String> {
    Ok(serde_json::json!({
        "pd": {"status": "running", "uptime": "2h 15m"},
        "st": {"status": "dormant", "pending_commits": 1},
        "sec": {"status": "standby", "last_check": "2026-11-06T16:45:00Z"},
        "ia": {"status": "active", "backend": "remote", "model": "gpt-4o-mini"},
        "permission_manager": {"status": "running", "socket": PERM_MGR_SOCKET}
    }))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            check_permission,
            list_commits,
            approve_commit,
            get_system_status
        ])
        .run(tauri::generate_context!())
        .expect("error while running TUPIOS Control Center");
}