# TUPIOS — Permission Manager Module
# Fase 5: Controle de permissões da IA, System API e Harness Engine
# Valida operações da IA contra manifesto antes da execução
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.permissionManager;

  socketPath = "/run/tupi-perm-mgr/perm-mgr.sock";

  # O daemon vive no Nix store (apps/tupi-agent/tupi_agent/permd.py), junto com a política
  # (policy.py). NUNCA escrever scripts em /run/current-system na ativação: foi isso que
  # quebrou o boot da ISO v3.
  agentPkg = import ./tupi-agent-package.nix { inherit pkgs; };
in
{
  options.tupi.permissionManager = {
    enable = lib.mkEnableOption "TUPIOS Permission Manager";

    policyFile = lib.mkOption {
      type = lib.types.str;
      default = "/etc/tupi/permission-policy.json";
      description = "Path to permission policy JSON file";
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
    # O grupo usado em tmpfiles, chown e no serviço precisa existir
    users.groups.tupi-ia = { };

    # === Audit Log Directory (append-only) ===
    systemd.tmpfiles.rules = [
      "d ${dirOf cfg.auditLogPath} 0750 root tupi-ia -"
      "f ${cfg.auditLogPath} 0640 root tupi-ia -"
    ];

    # Make audit log append-only via filesystem attribute (best effort: some
    # filesystems do not support chattr; never fail activation because of it)
    system.activationScripts.tupi-audit-immutable = lib.stringAfter [ "var" "users" "groups" ] ''
      mkdir -p "${dirOf cfg.auditLogPath}"
      touch "${cfg.auditLogPath}"
      chown root:tupi-ia "${dirOf cfg.auditLogPath}" "${cfg.auditLogPath}" 2>/dev/null || true
      chmod 0750 "${dirOf cfg.auditLogPath}" 2>/dev/null || true
      chmod 0640 "${cfg.auditLogPath}" 2>/dev/null || true
      ${pkgs.e2fsprogs}/bin/chattr +a "${cfg.auditLogPath}" 2>/dev/null || true
    '';

    # === Permission Manager Service ===
    systemd.services.tupi-perm-mgr = {
      description = "TUPIOS Permission Manager — validates AI operations against policy";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" "systemd-tmpfiles-setup.service" ];
      path = [ pkgs.e2fsprogs ];
      environment = {
        PYTHONPATH = "${agentPkg}/lib/python";
        TUPI_POLICY = cfg.policyFile;
        TUPI_AUDIT_LOG = cfg.auditLogPath;
        TUPI_AUTO_APPROVE_READONLY = if cfg.autoApproveReadonly then "1" else "0";
        TUPI_MAX_CONCURRENT = toString cfg.maxConcurrentRequests;
        TUPI_SOCKET = socketPath;
        TUPI_AGENT_BASE_POLICY = "/etc/tupi/agent-base-policy.json";
        TUPI_AGENT_POLICY = "/etc/tupi/agent-policy.json";
      };
      serviceConfig = {
        Type = "simple";
        User = "root";
        Group = "tupi-ia";
        ExecStart = "${pkgs.python3}/bin/python3 -u -m tupi_agent.permd";
        Restart = "on-failure";
        RestartSec = "3s";
        # systemd cria /run/tupi-perm-mgr ANTES de montar o sandbox
        # (com ProtectSystem=strict, um ReadWritePaths inexistente derruba o serviço)
        RuntimeDirectory = "tupi-perm-mgr";
        RuntimeDirectoryMode = "0750";
        PrivateTmp = true;
        ProtectSystem = "strict";
        # /etc/tupi: grava a política de runtime SÓ a pedido humano (policy_set)
        ReadWritePaths = [ "-${dirOf cfg.auditLogPath}" "-/etc/tupi" ];
        NoNewPrivileges = true;
        SystemCallFilter = [ "@system-service" ];
      };
    };

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
        set -eu
        SOCKET="${socketPath}"
        AGENT_ID="''${1:-cli}"
        OPERATION="''${2:-read}"
        TARGET="''${3:-}"

        if [ ! -S "$SOCKET" ]; then
          echo '{"decision":"deny","reason":"Permission Manager not running"}'
          exit 1
        fi

        REQUEST=$(${jq}/bin/jq -cn \
          --arg a "$AGENT_ID" --arg o "$OPERATION" --arg t "$TARGET" --arg r "$(date +%s%N)" \
          '{agent_id:$a, operation:$o, target:$t, request_id:$r}')

        RESPONSE=$(printf '%s' "$REQUEST" | ${socat}/bin/socat - UNIX-CONNECT:"$SOCKET" 2>/dev/null || \
                   echo '{"decision":"deny","reason":"socket error"}')
        echo "$RESPONSE"
      '')
      # Pre-commit hook that validates all pending operations through Permission Manager
      (writeShellScriptBin "tupi-harness-validate" ''
        set -eu
        MANIFEST="''${1:-/var/lib/tupi-st/pending-manifest.json}"
        SOCKET="${socketPath}"

        if [ ! -f "$MANIFEST" ]; then
          echo "No pending manifest found"
          exit 0
        fi

        echo "[Harness] Validating manifest: $MANIFEST"

        DENIED=0
        PENDING=0

        # Process substitution (não pipe) para os contadores sobreviverem ao loop
        while read -r OP TARGET; do
          [ -n "$OP" ] || continue
          REQUEST=$(${jq}/bin/jq -cn --arg o "$OP" --arg t "$TARGET" \
            '{agent_id:"harness", operation:$o, target:$t}')
          RESULT=$(printf '%s' "$REQUEST" | ${socat}/bin/socat - UNIX-CONNECT:"$SOCKET" 2>/dev/null || \
            echo '{"decision":"deny"}')

          DECISION=$(echo "$RESULT" | ${jq}/bin/jq -r '.decision // "deny"' 2>/dev/null || echo deny)
          case "$DECISION" in
            allow) echo "  ✅ $OP $TARGET" ;;
            pending) echo "  ⏳ $OP $TARGET (awaiting approval)"; PENDING=$((PENDING+1)) ;;
            *) echo "  ❌ $OP $TARGET"; DENIED=$((DENIED+1)) ;;
          esac
        done < <(${jq}/bin/jq -r '.operations[] | "\(.type) \(.target)"' "$MANIFEST" 2>/dev/null || true)

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
