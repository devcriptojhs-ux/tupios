# TUPIOS — Agent Orchestrator Module
# Fase 7: Orquestrador de agentes com Memory Engine e comunicação MCP
# Coordena múltiplos agentes IA com memória persistente e protocolo padronizado
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.agentOrchestrator;

  # Python script as a derivation to avoid heredoc parsing issues in Nix
  agentOrchScript = pkgs.writeTextFile {
    name = "agent-orchestrator.py";
    text = ''
      #!/usr/bin/env python3
      """TUPIOS Agent Orchestrator - MCP server with persistent Memory Engine."""
      import json, os, sys, socket, sqlite3, time, uuid
      from datetime import datetime, timedelta

      MEMORY_DB = os.environ.get("TUPI_MEMORY_DB", "/var/lib/tupi-agents/memory.db")
      MCP_SOCKET = os.environ.get("TUPI_MCP_SOCKET", "/run/tupi-agents/mcp.sock")
      MAX_AGENTS = int(os.environ.get("TUPI_MAX_AGENTS", "8"))
      RETENTION_DAYS = int(os.environ.get("TUPI_RETENTION_DAYS", "30"))
      PERM_SOCKET = os.environ.get("TUPI_PERM_SOCKET", "/run/tupi-perm-mgr/perm-mgr.sock")

      def get_db():
          conn = sqlite3.connect(MEMORY_DB)
          conn.row_factory = sqlite3.Row
          return conn

      def check_permission(agent_id, operation, target=""):
          try:
              req = json.dumps({
                  "agent_id": agent_id,
                  "operation": operation,
                  "target": target,
                  "request_id": str(uuid.uuid4())
              })
              s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
              s.connect(PERM_SOCKET)
              s.sendall(req.encode())
              resp = s.recv(4096).decode()
              s.close()
              return json.loads(resp)
          except Exception as e:
              return {"decision": "deny", "reason": f"perm_mgr_error: {e}"}

      def store_memory(agent_id, session_id, role, content, metadata=None):
          db = get_db()
          expires = None
          if RETENTION_DAYS > 0:
              expires = (datetime.now() + timedelta(days=RETENTION_DAYS)).isoformat()
          db.execute(
              "INSERT INTO agent_memory (agent_id, session_id, role, content, metadata, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
              (agent_id, session_id, role, content, json.dumps(metadata or {}), expires)
          )
          db.commit()
          db.close()

      def recall_memory(agent_id, limit=10, session_id=None):
          db = get_db()
          if session_id:
              rows = db.execute(
                  "SELECT * FROM agent_memory WHERE agent_id=? AND session_id=? ORDER BY created_at DESC LIMIT ?",
                  (agent_id, session_id, limit)
              ).fetchall()
          else:
              rows = db.execute(
                  "SELECT * FROM agent_memory WHERE agent_id=? ORDER BY created_at DESC LIMIT ?",
                  (agent_id, limit)
              ).fetchall()
          db.close()
          return [dict(r) for r in rows]

      def create_session(agent_id):
          session_id = str(uuid.uuid4())
          db = get_db()
          db.execute("INSERT INTO agent_sessions (id, agent_id) VALUES (?, ?)", (session_id, agent_id))
          db.commit()
          db.close()
          return session_id

      def send_mcp_message(from_agent, to_agent, message_type, payload):
          db = get_db()
          db.execute(
              "INSERT INTO mcp_messages (from_agent, to_agent, message_type, payload) VALUES (?, ?, ?, ?)",
              (from_agent, to_agent, message_type, json.dumps(payload))
          )
          db.commit()
          msg_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
          db.close()
          return msg_id

      def handle_mcp_request(data):
          try:
              req = json.loads(data)
              method = req.get("method", "")
              params = req.get("params", {})
              agent_id = req.get("agent_id", "unknown")

              if method == "memory/store":
                  perm = check_permission(agent_id, "write_user_data", "/var/lib/tupi-agents/memory")
                  if perm["decision"] != "allow":
                      return {"error": f"Permission denied: {perm['reason']}"}
                  store_memory(agent_id, params.get("session_id", ""), params.get("role", "unknown"), params.get("content", ""), params.get("metadata"))
                  return {"status": "stored", "agent_id": agent_id}

              elif method == "memory/recall":
                  memories = recall_memory(agent_id, limit=params.get("limit", 10), session_id=params.get("session_id"))
                  return {"memories": memories, "count": len(memories)}

              elif method == "session/create":
                  session_id = create_session(agent_id)
                  return {"session_id": session_id, "agent_id": agent_id}

              elif method == "message/send":
                  to_agent = params.get("to_agent", "")
                  perm = check_permission(agent_id, "network_egress", "mcp://" + to_agent)
                  if perm["decision"] != "allow":
                      return {"error": "Permission denied: " + perm.get("reason", "unknown")}
                  msg_id = send_mcp_message(agent_id, to_agent, params.get("type", "text"), params.get("payload", {}))
                  return {"message_id": msg_id, "status": "sent"}

              elif method == "agents/list":
                  return {"agents": ["code-assistant", "file-manager", "system-monitor"]}

              elif method == "health":
                  return {"status": "ok", "uptime": time.time(), "db": MEMORY_DB}

              else:
                  return {"error": f"Unknown method: {method}"}

          except Exception as e:
              return {"error": str(e)}

      if os.path.exists(MCP_SOCKET):
          os.unlink(MCP_SOCKET)

      server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
      server.bind(MCP_SOCKET)
      os.chmod(MCP_SOCKET, 0o660)
      server.listen(MAX_AGENTS)

      print(f"[AgentOrch] MCP server listening on {MCP_SOCKET}")
      print(f"[AgentOrch] Memory DB: {MEMORY_DB}")
      print(f"[AgentOrch] Retention: {RETENTION_DAYS} days")

      while True:
          conn, _ = server.accept()
          try:
              data = conn.recv(8192).decode('utf-8')
              if data:
                  response = handle_mcp_request(data)
                  conn.sendall(json.dumps(response).encode('utf-8'))
          except Exception as e:
              conn.sendall(json.dumps({"error": str(e)}).encode('utf-8'))
          finally:
              conn.close()
    '';
    executable = true;
    destination = "/share/tupi/agent-orchestrator.py";
  };

  # SQL schema as a derivation
  memorySchemaSql = pkgs.writeTextFile {
    name = "tupi-memory-schema.sql";
    text = ''
      CREATE TABLE IF NOT EXISTS agent_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME
      );
      CREATE INDEX IF NOT EXISTS idx_agent_memory_agent ON agent_memory(agent_id);
      CREATE INDEX IF NOT EXISTS idx_agent_memory_session ON agent_memory(session_id);
      CREATE INDEX IF NOT EXISTS idx_agent_memory_created ON agent_memory(created_at);

      CREATE TABLE IF NOT EXISTS agent_sessions (
        id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        ended_at DATETIME,
        status TEXT DEFAULT 'active',
        context_summary TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_agent_sessions_agent ON agent_sessions(agent_id);

      CREATE TABLE IF NOT EXISTS mcp_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_agent TEXT NOT NULL,
        to_agent TEXT NOT NULL,
        message_type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        acknowledged INTEGER DEFAULT 0
      );
      CREATE INDEX IF NOT EXISTS idx_mcp_messages_from ON mcp_messages(from_agent);
      CREATE INDEX IF NOT EXISTS idx_mcp_messages_to ON mcp_messages(to_agent);
    '';
  };
in
{
  options.tupi.agentOrchestrator = {
    enable = lib.mkEnableOption "TUPIOS Agent Orchestrator";

    memoryDbPath = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/tupi-agents/memory.db";
      description = "Path to SQLite memory database for agent context persistence";
    };

    mcpSocketPath = lib.mkOption {
      type = lib.types.str;
      default = "/run/tupi-agents/mcp.sock";
      description = "Unix socket path for MCP (Model Context Protocol) communication";
    };

    maxAgents = lib.mkOption {
      type = lib.types.int;
      default = 8;
      description = "Maximum concurrent agents allowed";
    };

    memoryRetentionDays = lib.mkOption {
      type = lib.types.int;
      default = 30;
      description = "Days to retain agent memory before archival";
    };

    agentRegistry = lib.mkOption {
      type = lib.types.attrsOf (lib.types.submodule {
        options = {
          name = lib.mkOption { type = lib.types.str; };
          role = lib.mkOption { type = lib.types.enum [ "code-assistant" "file-manager" "system-monitor" "security-auditor" "researcher" ]; };
          permissions = lib.mkOption { type = lib.types.listOf lib.types.str; default = []; };
          model = lib.mkOption { type = lib.types.str; default = "gpt-4o-mini"; };
          enabled = lib.mkOption { type = lib.types.bool; default = true; };
        };
      });
      default = {
        "code-assistant" = {
          name = "Code Assistant";
          role = "code-assistant";
          permissions = [ "read" "write_user_data" "execute_binary" ];
          model = "gpt-4o-mini";
        };
        "file-manager" = {
          name = "File Manager";
          role = "file-manager";
          permissions = [ "read" "write_user_data" ];
          model = "gpt-4o-mini";
        };
        "system-monitor" = {
          name = "System Monitor";
          role = "system-monitor";
          permissions = [ "read" "network_egress" ];
          model = "gpt-4o-mini";
        };
      };
      description = "Registry of available agents with roles and permissions";
    };
  };

  config = lib.mkIf cfg.enable {
    # === Memory Database Directory ===
    systemd.tmpfiles.rules = [
      "d /var/lib/tupi-agents 0750 root tupi-ia -"
      "f ${cfg.memoryDbPath} 0640 root tupi-ia -"
    ];

    # Initialize memory database schema on first boot
    system.activationScripts.tupi-memory-init = lib.stringAfter [ "var" ] ''
      mkdir -p /var/lib/tupi-agents
      if [ ! -f "${cfg.memoryDbPath}" ]; then
        ${pkgs.sqlite}/bin/sqlite3 "${cfg.memoryDbPath}" < ${memorySchemaSql}
        chown root:tupi-ia "${cfg.memoryDbPath}"
        chmod 0640 "${cfg.memoryDbPath}"
      fi
    '';

    # === Agent Orchestrator Service ===
    systemd.services.tupi-agent-orchestrator = {
      description = "TUPIOS Agent Orchestrator - MCP server with Memory Engine";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" "tupi-perm-mgr.service" ];
      wants = [ "tupi-perm-mgr.service" ];
      serviceConfig = {
        Type = "simple";
        User = "root";
        Group = "tupi-ia";
        Restart = "on-failure";
        RestartSec = "3s";
        PrivateTmp = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ "/var/lib/tupi-agents" "/run/tupi-agents" ];
        NoNewPrivileges = false;
        SystemCallFilter = "@system-service @file-system @network-io @process";
      };
      script = ''
        mkdir -p /run/tupi-agents
        export TUPI_MEMORY_DB="${cfg.memoryDbPath}"
        export TUPI_MCP_SOCKET="${cfg.mcpSocketPath}"
        export TUPI_MAX_AGENTS="${toString cfg.maxAgents}"
        export TUPI_RETENTION_DAYS="${toString cfg.memoryRetentionDays}"
        export TUPI_PERM_SOCKET="/run/tupi-perm-mgr/perm-mgr.sock"

        echo "[AgentOrch] Starting MCP server on $TUPI_MCP_SOCKET"
        echo "[AgentOrch] Memory DB: $TUPI_MEMORY_DB"
        echo "[AgentOrch] Max agents: $TUPI_MAX_AGENTS"

        ${pkgs.python3}/bin/python3 -u ${agentOrchScript}/share/tupi/agent-orchestrator.py
      '';
    };

    # === MCP Client CLI ===
    environment.systemPackages = with pkgs; [
      socat
      jq
      sqlite
      (writeShellScriptBin "tupi-mcp" ''
        #!/bin/sh
        set -eu
        SOCKET="''${TUPI_MCP_SOCKET:-/run/tupi-agents/mcp.sock}"
        METHOD="''${1:-health}"
        AGENT_ID="''${2:-cli}"
        PARAMS="''${3:-{}}"

        if [ ! -S "$SOCKET" ]; then
          echo '{"error":"Agent Orchestrator not running"}'
          exit 1
        fi

        REQUEST=$(printf '{"method":"%s","agent_id":"%s","params":%s}' "$METHOD" "$AGENT_ID" "$PARAMS")
        RESPONSE=$(echo "$REQUEST" | ${socat}/bin/socat - UNIX-CONNECT:"$SOCKET" 2>/dev/null || \
          echo '{"error":"socket error"}')
        echo "$RESPONSE" | ${jq}/bin/jq .
      '')
      (writeShellScriptBin "tupi-memory" ''
        #!/bin/sh
        set -eu
        DB="''${TUPI_MEMORY_DB:-/var/lib/tupi-agents/memory.db}"
        CMD="''${1:-list}"
        AGENT_ID="''${2:-}"

        case "$CMD" in
          list)
            if [ -n "$AGENT_ID" ]; then
              ${sqlite}/bin/sqlite3 "$DB" "SELECT id, agent_id, role, substr(content,1,80), created_at FROM agent_memory WHERE agent_id='$AGENT_ID' ORDER BY created_at DESC LIMIT 20;"
            else
              ${sqlite}/bin/sqlite3 "$DB" "SELECT id, agent_id, role, substr(content,1,80), created_at FROM agent_memory ORDER BY created_at DESC LIMIT 20;"
            fi
            ;;
          sessions)
            ${sqlite}/bin/sqlite3 "$DB" "SELECT id, agent_id, started_at, status FROM agent_sessions ORDER BY started_at DESC LIMIT 20;"
            ;;
          messages)
            ${sqlite}/bin/sqlite3 "$DB" "SELECT id, from_agent, to_agent, message_type, created_at FROM mcp_messages ORDER BY created_at DESC LIMIT 20;"
            ;;
          cleanup)
            DAYS="''${3:-30}"
            DELETED=$(${sqlite}/bin/sqlite3 "$DB" "DELETE FROM agent_memory WHERE expires_at < datetime('now'); SELECT changes();")
            echo "Cleaned up $DELETED expired memories"
            ;;
          *)
            echo "Usage: tupi-memory {list|sessions|messages|cleanup} [agent_id] [days]"
            ;;
        esac
      '')
    ];

    # === Agent Registry Config ===
    environment.etc."tupi/agent-registry.json".text = builtins.toJSON cfg.agentRegistry;
  };
}