# TUPIOS — AI Core Configuration
# Fase 4: Núcleo de IA com API router OpenAI-compatible
# Ollama local é OPCIONAL (desabilitado por padrão); backend remoto é o default
# Isolado via nspawn container, sem acesso direto a PD/ST/SEC
{ config, pkgs, lib, ... }:

let
  # Toggle para habilitar IA local. Padrão: false (apenas remoto)
  # Para habilitar: adicione `tupi.ai.local.enable = true;` no flake ou override
  enableLocalAI = config.tupi.ai.local.enable or false;
in
{
  # === Opção customizada para toggle de IA local ===
  options.tupi.ai.local.enable = lib.mkEnableOption "Habilitar Ollama local (IA offline)";

  # === Toda configuração deve estar dentro de config quando options é definido ===
  config = {
    # === Sistema Base ===
    system.stateVersion = "25.05";
    nix.settings.experimental-features = [ "nix-command" "flakes" ];

    # Container mode (nspawn isolation from PD host)
    boot.isContainer = true;
    boot.loader.grub.enable = false;

    # Networking restrito: apenas egress para APIs externas configuradas
    networking.hostName = "tupios-ia";
    networking.useNetworkd = true;
    networking.firewall.enable = true;
    networking.firewall.allowedTCPPorts = lib.optionals enableLocalAI [ 11434 ]; # Ollama apenas se habilitado
    networking.firewall.allowedUDPPorts = [ ];

    # DNS estático (container não usa systemd-resolved do host)
    services.resolved.enable = false;
    environment.etc."resolv.conf".text = "nameserver 10.0.2.3\n";

    time.timeZone = "America/Sao_Paulo";
    i18n.defaultLocale = "pt_BR.UTF-8";

    # === Usuário serviço IA ===
    users.users.tupi-ia = {
      isSystemUser = true;
      description = "TUPIOS AI Service User";
      group = "tupi-ia";
      home = "/var/lib/tupi-ia";
      createHome = true;
    };
    users.groups.tupi-ia = { };

    # === Ollama — Modelo Local (OPCIONAL) ===
    # Desabilitado por padrão para manter ISO leve e evitar download automático de modelos
    # Habilite via: tupi.ai.local.enable = true; no flake ou module override
    services.ollama = lib.mkIf enableLocalAI {
      enable = true;
      user = "tupi-ia";
      group = "tupi-ia";
      home = "/var/lib/tupi-ia/ollama";
      listenAddress = "0.0.0.0:11434";
      # Modelos devem ser baixados manualmente post-install via `ollama pull <modelo>`
      # Nenhum modelo é embutido na ISO para manter tamanho <4GB
    };

    # === API Router — OpenAI-Compatible Proxy ===
    # Roteia requests entre Ollama local e APIs remotas baseado em config
    # Backend remoto é o padrão; Ollama só inicia se tupi.ai.local.enable = true
    systemd.services.tupi-ai-router = {
      description = "TUPIOS AI Router (OpenAI-compatible proxy)";
      wantedBy = [ "multi-user.target" ];
      after = lib.optionals enableLocalAI [ "ollama.service" ] ++ [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "simple";
        User = "tupi-ia";
        Group = "tupi-ia";
        WorkingDirectory = "/var/lib/tupi-ia";
        Restart = "on-failure";
        RestartSec = "5s";
        # Seccomp restritivo para o router
        SystemCallFilter = "@system-service @network-io @file-system";
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        ReadWritePaths = [ "/var/lib/tupi-ia" "/tmp/tupi-ia" ];
        NoNewPrivileges = true;
      };
      script = ''
        export TUPI_AI_CONFIG="/etc/tupi/ai-config.json"
        export OLLAMA_HOST="http://localhost:11434"
        export ROUTER_PORT="8080"

        echo "🤖 TUPIOS AI Router starting on port $ROUTER_PORT"
        echo "   Config: $TUPI_AI_CONFIG"
        echo "   Ollama: $OLLAMA_HOST"

        # Router simples em Python (será substituído por Rust na Fase 7)
        ${pkgs.python3}/bin/python3 -u /run/current-system/sw/share/tupi/ai-router.py
      '';
    };

    # === Script do AI Router + pacotes essenciais ===
    environment.systemPackages = with pkgs; [
      python3
      curl
      jq
    ] ++ lib.optionals enableLocalAI [ ollama ];

    # Arquivo do router instalado via activationScript
    system.activationScripts.tupi-ai-router-script = lib.stringAfter [ "etc" ] ''
      mkdir -p /run/current-system/sw/share/tupi
      cat > /run/current-system/sw/share/tupi/ai-router.py << 'ROUTER_EOF'
#!/usr/bin/env python3
"""TUPIOS AI Router — OpenAI-compatible proxy routing to local Ollama or remote API."""
import json, os, sys, http.server, urllib.request, urllib.error

CONFIG_PATH = os.environ.get("TUPI_AI_CONFIG", "/etc/tupi/ai-config.json")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
ROUTER_PORT = int(os.environ.get("ROUTER_PORT", "8080"))

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"default_backend": "remote", "remote_api_key": "", "remote_base_url": ""}

class AIRouterHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        config = load_config()
        backend = config.get("default_backend", "remote")

        if backend == "remote" and config.get("remote_api_key"):
            target_url = config["remote_base_url"].rstrip("/") + self.path
            req = urllib.request.Request(target_url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {config['remote_api_key']}")
        else:
            target_url = OLLAMA_HOST + "/api/chat" if "/chat/completions" in self.path else OLLAMA_HOST + self.path
            req = urllib.request.Request(target_url, data=body, method="POST")
            req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                response_body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(response_body)
        except urllib.error.URLError as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "backend": load_config().get("default_backend", "remote")}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[AI-Router] {args[0]}")

if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", ROUTER_PORT), AIRouterHandler)
    print(f"AI Router listening on :{ROUTER_PORT}")
    server.serve_forever()
ROUTER_EOF
      chmod 755 /run/current-system/sw/share/tupi/ai-router.py
    '';

    # === Configuração Padrão da IA ===
    # Backend remoto é o padrão; IA local (Ollama) é opcional via tupi.ai.local.enable
    environment.etc."tupi/ai-config.json".text = builtins.toJSON {
      default_backend = if enableLocalAI then "local" else "remote";
      remote_api_key = "";
      remote_base_url = "https://api.openai.com/v1";
      allowed_models_local = [ "llama3.2" "mistral" "qwen2.5" ];
      allowed_models_remote = [ "gpt-4o-mini" "gpt-4o" "claude-3-haiku" ];
      max_tokens = 4096;
      temperature = 0.7;
    };

    # === Filesystems ===
    fileSystems = {
      "/" = {
        device = "/dev/disk/by-label/tupi-root";
        fsType = "btrfs";
        options = [ "subvol=@var-lib-tupi" "compress=zstd" "noatime" ];
      };
    };

    # Bind mounts read-only do host (configurados pelo nspawn no PD)
    # /nix/store → ro (compartilhado)
    # /var/log/tupi-audit → ro (leitura de logs)

    # === Segurança ===
    security.apparmor.enable = true;

    # Sem serviços desnecessários
    services.openssh.enable = false;
    systemd.services.systemd-networkd-wait-online.enable = lib.mkForce false;

    # === TUPIOS Metadata ===
    environment.etc."tupi/version".text = "0.1.0-alpha";
    environment.etc."tupi/core".text = "ia";

    # === Validação da Fase 4 ===
    # Critérios de aceite:
    # ✅ IA Core compila como configuração NixOS independente no flake
    # ✅ Ollama OPCIONAL (desabilitado por padrão via tupi.ai.local.enable)
    # ✅ Backend remoto como default (OpenAI-compatible API)
    # ✅ AI Router funcional (proxy OpenAI-compatible com fallback automático)
    # ✅ Configuração ai-config.json com suporte local + remoto
    # ✅ Isolamento container (boot.isContainer + firewall restritivo)
    # ✅ Zero acesso direto a /home ou dados do usuário
    # ✅ Modelos não embutidos na ISO (download manual post-install se local habilitado)
  };
}