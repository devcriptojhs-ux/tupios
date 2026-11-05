# TUPIOS — Permission Manager Module
# Fase 5: Controle de permissões da IA, System API e Harness Engine
# Valida operações da IA contra manifesto antes da execução
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.permissionManager;
in
{
  options.tupi.permissionManager = {
    enable = lib.mkEnableOption "TUPIOS Permission Manager";

    policyFile = lib.mkOption {
      type = lib.types.path;
      default = "${config.environment.etc."tupi/permission-policy.json".source}";
      description = "Path to permission policy JSON file (auto-resolved from etc activation)";
    };

    auditLogPath = lib.mkOption {
      type = lib.types.str;
      default = "/var/log/tupi-audit/permission.log";
      description = "Path to append-only audit log";
    };

    autoApproveReadonly = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Auto-approve read-only operations without user confirmation";
    };

    maxConcurrentRequests = lib.mkOption {
      type = lib.types.int;
      default = 10;
      description = "Maximum concurrent permission requests before queueing";
    };
  };

  config = lib.mkIf cfg.enable {
    # === Audit Log Directory (append-only) ===
    systemd.tmpfiles.rules = [
      "d /var/log/tupi-audit 0750 root tupi-ia -"
      "f ${cfg.auditLogPath} 0640 root tupi-ia -"
    ];

    # Make audit log append-only via filesystem attribute
    system.activationScripts.tupi-audit-immutable = lib.stringAfter [ "var" ] ''
      mkdir -p /var/log/tupi-audit
      touch "${cfg.auditLogPath}"
      chown root:tupi-ia "${cfg.auditLogPath}" 2>/dev/null || true
      chmod 0640 "${cfg.auditLogPath}" 2>/dev/null || true
      chattr +a "${cfg.auditLogPath}" 2>/dev/null || true
    '';

    # === Permission Manager Service ===
    systemd.services.tupi-perm-mgr = {
      description = "TUPIOS Permission Manager — validates AI operations against policy";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" ];
      serviceConfig = {
        Type = "simple";
        User = "root";
        Group = "tupi-ia";
        Restart = "on-failure";
        RestartSec = "3s";
        PrivateTmp = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ "/var/log/tupi-audit" "/run/tupi-perm-mgr" ];
        NoNewPrivileges = false; # Needs to manage other services
        SystemCallFilter = "@system-service @file-system @network-io @process";
      };
      script = ''
        mkdir -p /run/tupi-perm-mgr
        export TUPI_POLICY="${cfg.policyFile}"
        export TUPI_AUDIT_LOG="${cfg.auditLogPath}"
        export TUPI_AUTO_APPROVE_READONLY="${if cfg.autoApproveReadonly then "1" else "0"}"
        export TUPI_MAX_CONCURRENT="${toString cfg.maxConcurrentRequests}"
        export TUPI_SOCKET="/run/tupi-perm-mgr/perm-mgr.sock"

        echo "[PermMgr] Starting on $TUPI_SOCKET"
        ${pkgs.python3}/bin/python3 -u /run/current-system/sw/share/tupi/perm-mgr.py
      '';
    };

    # === Permission Manager Python Implementation ===
    system.activationScripts.tupi-perm-mgr-script = lib.stringAfter [ "etc" ] ''
      mkdir -p /run/current-system/sw/share/tupi
      cat > /run/current-system/sw/share/tupi/perm-mgr.py << 'PERMMGR_EOF'
#!/usr/bin/env python3
"""TUPIOS Permission Manager — validates AI operations against declarative policy."""
import json, os, sys, socket, struct, time, hashlib, hmac
from pathlib import Path

POLICY_PATH = os.environ.get("TUPI_POLICY", "/etc/tupi/permission-policy.json")
AUDIT_LOG = os.environ.get("TUPI_AUDIT_LOG", "/var/log/tupi-audit/permission.log")
AUTO_APPROVE_RO = os.environ.get("TUPI_AUTO_APPROVE_READONLY", "1") == "1"
MAX_CONCURRENT = int(os.environ.get("TUPI_MAX_CONCURRENT", "10"))
SOCKET_PATH = os.environ.get("TUPI_SOCKET", "/run/tupi-perm-mgr/perm-mgr.sock")

# Operation categories and their risk levels
RISK_LEVELS = {
    "read": 0,
    "write_user_data": 1,
    "write_system_config": 2,
    "install_package": 2,
    "network_egress": 1,
    "execute_binary": 2,
    "modify_bootloader": 3,
    "access_hardware": 3,
    "privilege_escalation": 3,
    "delete_system_file": 3,
}

def load_policy():
    try:
        with open(POLICY_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"default_action": "deny", "rules": []}

def classify_operation(op_type, target_path=None):
    """Classify an operation into a risk category."""
    op = op_type.lower()
    if op in ("read", "list", "stat", "cat"):
        return "read"
    elif op in ("write", "create", "mkdir"):
        if target_path and ("/home/" in target_path or "/var/lib/tupi-st/" in target_path):
            return "write_user_data"
        return "write_system_config"
    elif op in ("install", "nix-build", "nix-env"):
        return "install_package"
    elif op in ("curl", "wget", "fetch", "http"):
        return "network_egress"
    elif op in ("exec", "run", "spawn"):
        return "execute_binary"
    elif op in ("bootctl", "systemd-boot", "grub"):
        return "modify_bootloader"
    elif op in ("sysfs", "dev", "hw"):
        return "access_hardware"
    elif op in ("sudo", "su", "setuid", "cap_"):
        return "privilege_escalation"
    elif op in ("rm", "delete", "unlink"):
        if target_path and ("/nix/store" in target_path or "/etc" in target_path):
            return "delete_system_file"
        return "write_user_data"
    return "write_system_config"

def check_policy(policy, category, agent_id="unknown"):
    """Check if operation is allowed by policy."""
    # Check specific rules first
    for rule in policy.get("rules", []):
        if rule.get("category") == category:
            if rule.get("agents") and agent_id not in rule["agents"]:
                continue
            return rule.get("action", "deny")

    # Fall back to default
    return policy.get("default_action", "deny")

def audit_log(agent_id, operation, category, decision, reason=""):
    """Append to immutable audit log."""
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    entry = {
        "timestamp": timestamp,
        "agent_id": agent_id,
        "operation": operation,
        "category": category,
        "decision": decision,
        "reason": reason,
        "hash": ""
    }
    line = json.dumps(entry, separators=(',', ':'))

    # Chain hash for tamper detection
    try:
        with open(AUDIT_LOG, 'r') as f:
            last_line = f.readlines()[-1].strip() if f.readable() else ""
        prev_hash = json.loads(last_line).get("hash", "genesis") if last_line else "genesis"
    except:
        prev_hash = "genesis"

    entry["hash"] = hashlib.sha256(f"{prev_hash}:{line}".encode()).hexdigest()[:16]

    # Temporarily remove append-only to write
    os.system(f"chattr -a '{AUDIT_LOG}' 2>/dev/null")
    with open(AUDIT_LOG, 'a') as f:
        f.write(json.dumps(entry, separators=(',', ':')) + "\n")
    os.system(f"chattr +a '{AUDIT_LOG}' 2>/dev/null")

def handle_request(data):
    """Process a permission request."""
    try:
        req = json.loads(data)
        agent_id = req.get("agent_id", "unknown")
        operation = req.get("operation", "")
        target = req.get("target", "")

        category = classify_operation(operation, target)
        policy = load_policy()

        # Auto-approve read-only if configured
        if AUTO_APPROVE_RO and category == "read":
            decision = "allow"
            reason = "auto-approved: read-only"
        else:
            action = check_policy(policy, category, agent_id)
            if action == "allow":
                decision = "allow"
                reason = "policy: explicit allow"
            elif action == "ask":
                decision = "pending"
                reason = "requires user approval"
            else:
                decision = "deny"
                reason = f"policy: denied (category={category})"

        audit_log(agent_id, operation, category, decision, reason)

        return json.dumps({
            "decision": decision,
            "category": category,
            "reason": reason,
            "request_id": req.get("request_id", "")
        })
    except Exception as e:
        return json.dumps({"decision": "deny", "reason": f"error: {str(e)}"})

# Unix socket server
if os.path.exists(SOCKET_PATH):
    os.unlink(SOCKET_PATH)

server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET_PATH)
os.chmod(SOCKET_PATH, 0o660)
server.listen(MAX_CONCURRENT)

print(f"[PermMgr] Listening on {SOCKET_PATH}")
print(f"[PermMgr] Policy: {POLICY_PATH}")
print(f"[PermMgr] Audit: {AUDIT_LOG}")

while True:
    conn, _ = server.accept()
    try:
        data = conn.recv(4096).decode('utf-8')
        if data:
            response = handle_request(data)
            conn.sendall(response.encode('utf-8'))
    except Exception as e:
        conn.sendall(json.dumps({"decision": "deny", "reason": str(e)}).encode('utf-8'))
    finally:
        conn.close()
PERMMGR_EOF
      chmod 755 /run/current-system/sw/share/tupi/perm-mgr.py
    '';

    # === Default Permission Policy ===
    environment.etc."tupi/permission-policy.json".text = builtins.toJSON {
      version = "1.0";
      default_action = "deny";
      rules = [
        {
          category = "read";
          action = "allow";
          description = "All agents can read files";
        }
        {
          category = "write_user_data";
          action = "allow";
          agents = [ "code-assistant" "file-manager" ];
          description = "Trusted agents can write to user data";
        }
        {
          category = "write_system_config";
          action = "ask";
          description = "System config changes require approval";
        }
        {
          category = "install_package";
          action = "ask";
          description = "Package installation requires approval";
        }
        {
          category = "network_egress";
          action = "ask";
          description = "Network access requires approval";
        }
        {
          category = "execute_binary";
          action = "ask";
          description = "Binary execution requires approval";
        }
        {
          category = "modify_bootloader";
          action = "deny";
          description = "Bootloader modifications never allowed via AI";
        }
        {
          category = "access_hardware";
          action = "deny";
          description = "Direct hardware access never allowed via AI";
        }
        {
          category = "privilege_escalation";
          action = "deny";
          description = "Privilege escalation never allowed";
        }
        {
          category = "delete_system_file";
          action = "deny";
          description = "System file deletion never allowed via AI";
        }
      ];
    };

    # === System API Client Library + Harness Engine ===
    environment.systemPackages = with pkgs; [
      socat
      jq
      (writeShellScriptBin "tupi-perm-check" ''
        #!/bin/sh
        set -eu
        SOCKET="/run/tupi-perm-mgr/perm-mgr.sock"
        AGENT_ID="''${1:-cli}"
        OPERATION="''${2:-read}"
        TARGET="''${3:-}"

        if [ ! -S "$SOCKET" ]; then
          echo '{"decision":"deny","reason":"Permission Manager not running"}'
          exit 1
        fi

        REQUEST=$(printf '{"agent_id":"%s","operation":"%s","target":"%s","request_id":"%s"}' \
          "$AGENT_ID" "$OPERATION" "$TARGET" "$(date +%s%N)")

        RESPONSE=$(echo "$REQUEST" | ${pkgs.socat}/bin/socat - UNIX-CONNECT:"$SOCKET" 2>/dev/null || \
                   echo '{"decision":"deny","reason":"socket error"}')
        echo "$RESPONSE"
      '')
      # Pre-commit hook that validates all pending operations through Permission Manager
      (writeShellScriptBin "tupi-harness-validate" ''
        #!/bin/sh
        set -eu
        MANIFEST="''${1:-/var/lib/tupi-st/pending-manifest.json}"
        SOCKET="/run/tupi-perm-mgr/perm-mgr.sock"

        if [ ! -f "$MANIFEST" ]; then
          echo "No pending manifest found"
          exit 0
        fi

        echo "[Harness] Validating manifest: $MANIFEST"

        DENIED=0
        PENDING=0

        ${pkgs.jq}/bin/jq -r '.operations[] | "\(.type) \(.target)"' "$MANIFEST" 2>/dev/null | while read OP TARGET; do
          RESULT=$(${pkgs.socat}/bin/socat - UNIX-CONNECT:"$SOCKET" <<< \
            "{\"agent_id\":\"harness\",\"operation\":\"$OP\",\"target\":\"$TARGET\"}" 2>/dev/null || \
            echo '{"decision":"deny"}')

          DECISION=$(echo "$RESULT" | ${pkgs.jq}/bin/jq -r '.decision')
          case "$DECISION" in
            allow) echo "  ✅ $OP $TARGET" ;;
            pending) echo "  ⏳ $OP $TARGET (awaiting approval)"; PENDING=$((PENDING+1)) ;;
            deny) echo "  ❌ $OP $TARGET"; DENIED=$((DENIED+1)) ;;
          esac
        done

        if [ "$DENIED" -gt 0 ]; then
          echo "[Harness] ❌ Validation FAILED: $DENIED operations denied"
          exit 1
        elif [ "$PENDING" -gt 0 ]; then
          echo "[Harness] ⏳ Validation PENDING: $PENDING operations need approval"
          exit 2
        else
          echo "[Harness] ✅ All operations approved"
          exit 0
        fi
      '')
    ];
  };
}