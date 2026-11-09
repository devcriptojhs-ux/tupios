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
  # Router compartilhado com ISO e PD (nixos/modules/ai-router.nix)
  imports = [ ../modules/ai-router.nix ];

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
      # O módulo do ollama também define users.users.tupi-ia.home = home,
      # então precisa ser o mesmo valor declarado acima (senão: conflito de opção).
      home = "/var/lib/tupi-ia";
      models = "/var/lib/tupi-ia/ollama/models";
      # `listenAddress` foi removido do NixOS; agora são host + port
      host = "0.0.0.0";
      port = 11434;
      # Modelos devem ser baixados manualmente post-install via `ollama pull <modelo>`
      # Nenhum modelo é embutido na ISO para manter tamanho <4GB
    };

    # ReadWritePaths do ollama exige que o diretório de modelos já exista
    systemd.tmpfiles.rules = lib.optionals enableLocalAI [
      "d /var/lib/tupi-ia/ollama 0750 tupi-ia tupi-ia -"
      "d /var/lib/tupi-ia/ollama/models 0750 tupi-ia tupi-ia -"
    ];

    # === API Router — OpenAI-Compatible Proxy ===
    # Mesmo router da ISO/PD (nixos/modules/ai-router.nix). Backend remoto é o
    # padrão; com tupi.ai.local.enable = true o padrão passa a ser o Ollama local.
    # Escuta em 0.0.0.0 para o PD alcançar via veth; a porta continua fechada no
    # firewall (abra com tupi.aiRouter.openFirewall = true quando o veth existir).
    tupi.aiRouter = {
      enable = true;
      listenAddress = "0.0.0.0";
      defaultBackend = if enableLocalAI then "local" else "remote";
    };

    # === Pacotes essenciais ===
    environment.systemPackages = with pkgs; [
      python3
      curl
      jq
    ] ++ lib.optionals enableLocalAI [ ollama ];

    # === Configuração da IA ===
    # /etc/tupi/ai-config.json é gerado por `sudo tupi-ai-setup` (chave fica 0640 root:tupi-ia).
    # Não é gerenciado pelo Nix: um environment.etc viraria symlink read-only para o
    # store e o tupi-ai-setup não conseguiria gravar a chave.

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
    # ✅ AI Router funcional (proxy OpenAI-compatible, módulo compartilhado)
    # ✅ Configuração ai-config.json (tupi-ai-setup) com suporte local + remoto
    # ✅ Isolamento container (boot.isContainer + firewall restritivo)
    # ✅ Zero acesso direto a /home ou dados do usuário
    # ✅ Modelos não embutidos na ISO (download manual post-install se local habilitado)
  };
}
