# TUPIOS — Production Domain (PD) Configuration
# Fase 1: NixOS Personalizado com PD Funcional
{ config, pkgs, lib, ... }:

let
  # Mudanças que a IA ensaiou no ST e você aprovou (st_promote): mantidas pelo tupi-agent-exec.
  #   agent-config.json: pacotes, sysctl e parâmetros de boot   agent-extra.nix: trechos de módulo NixOS
  agentConfigFile = ./agent-config.json;
  agentConfig = if builtins.pathExists agentConfigFile then lib.importJSON agentConfigFile else { };
  agentPackage = n:
    lib.attrByPath (lib.splitString "." n) (throw "agent-config.json: pacote desconhecido '${n}'") pkgs;
in
{
  # === Sistema Base ===
  system.stateVersion = "25.05";
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Bootloader systemd-boot (UEFI)
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  # Kernel + firmware
  boot.kernelPackages = pkgs.linuxPackages_latest;
  hardware.enableRedistributableFirmware = true;

  # Permitir pacotes unfree quando necessário (firmware, drivers)
  nixpkgs.config.allowUnfree = true;

  # Networking básico
  networking.hostName = "tupios-pd";
  networking.networkmanager.enable = true;

  # Timezone e locale
  time.timeZone = "America/Sao_Paulo";
  i18n.defaultLocale = "pt_BR.UTF-8";
  console.keyMap = "br-abnt2";

  # === Usuário padrão ===
  users.users.tupi = {
    isNormalUser = true;
    description = "TUPIOS User";
    # tupi-ia: configurar a IA (Configurar IA / tupi-ai-setup) sem sudo
    extraGroups = [ "wheel" "networkmanager" "video" "audio" "tupi-ia" ];
    shell = pkgs.zsh;
  };

  # === Sway (sessão alternativa, escolhida na tela de login) ===
  # A sessão padrão é a área de trabalho estilo Windows (tupi.classicDesktop).
  programs.sway = {
    enable = true;
    wrapperFeatures.gtk = true;
    extraPackages = with pkgs; [
      swaybg          # Wallpaper
      swayidle        # Idle management
      swaylock        # Screen locker
      waybar          # Status bar
      wofi            # App launcher
      mako            # Notifications
      grim            # Screenshots
      slurp           # Region selection
      wl-clipboard    # Clipboard
      brightnessctl   # Brightness control
      pulseaudio      # Audio (Wayland compat)
    ];
  };

  # XDG Portal para Wayland
  xdg.portal = {
    enable = true;
    wlr.enable = true;
    extraPortals = with pkgs; [
      xdg-desktop-portal-wlr
      xdg-desktop-portal-gtk
    ];
  };

  # === Fontes ===
  fonts.packages = with pkgs; [
    terminus_font
    noto-fonts
    noto-fonts-cjk-sans
    font-awesome
    jetbrains-mono
  ];

  # === Aplicações Essenciais ===
  environment.systemPackages = with pkgs; [
    # Terminal
    alacritty
    zsh
    starship

    # File Manager
    pcmanfm       # Leve e estável para Fase 1
    gvfs          # Mount support

    # System Monitor (Fase 1 - básico)
    btop

    # Editor
    helix

    # Utilitários
    htop
    tree
    ripgrep
    fd
    bat
    eza
    git
    curl
    wget
    unzip
    p7zip

    # TUPIOS tools
    btrfs-progs
  ]
  # Programas instalados pela IA (ensaiados no ST e aprovados por você)
  ++ map agentPackage (agentConfig.pacotes or [ ]);

  # Parâmetros do kernel tornados permanentes pela IA (com aprovação)
  boot.kernel.sysctl = agentConfig.sysctl or { };
  boot.kernelParams = agentConfig.parametros_kernel or [ ];

  # === ZSH como shell padrão ===
  programs.zsh = {
    enable = true;
    autosuggestions.enable = true;
    syntaxHighlighting.enable = true;
    ohMyZsh = {
      enable = true;
      plugins = [ "git" "sudo" "history" ];
      theme = "robbyrussell";
    };
  };

  # === Services ===
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    pulse.enable = true;
  };

  # D-Bus (necessário para Wayland apps)
  services.dbus.enable = true;

  # Polkit para autenticação GUI
  security.polkit.enable = true;

  # === Btrfs Subvolumes (documentação / setup manual na instalação) ===
  # Layout esperado:
  #   @pd-root   → /
  #   @home      → /home
  #   @nix-store → /nix/store
  #   @var-lib-tupi → /var/lib/tupi-*
  #
  # Montagem declarativa via fileSystems abaixo assume que o instalador
  # (scripts/tupi-partition.sh) criou os subvolumes corretamente.

  # O tupi-partition.sh coloca o Btrfs DENTRO de LUKS (partição GPT "TUPI-ROOT").
  # Sem isto o initrd não pede a senha, não abre o LUKS e o boot do PD trava
  # esperando o dispositivo root. Os mounts usam /dev/mapper/tupi-root porque o
  # LUKS e o Btrfs têm o MESMO label ("tupi-root"): /dev/disk/by-label é ambíguo.
  boot.initrd.luks.devices."tupi-root" = {
    device = "/dev/disk/by-partlabel/TUPI-ROOT";
    allowDiscards = true;
  };

  fileSystems = {
    "/" = {
      device = "/dev/mapper/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@pd-root" "compress=zstd" "noatime" ];
    };
    "/home" = {
      device = "/dev/mapper/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@home" "compress=zstd" "noatime" ];
    };
    "/nix/store" = {
      device = "/dev/mapper/tupi-root";
      fsType = "btrfs";
      # Sem "ro": o NixOS já remonta /nix/store read-only (boot.readOnlyNixStore)
      # e só o nix-daemon escreve nele. Com "ro" aqui, nixos-rebuild/nix build quebram.
      options = [ "subvol=@nix-store" "compress=zstd" "noatime" ];
    };
    "/boot" = {
      device = "/dev/disk/by-label/EFI";
      fsType = "vfat";
    };
  };

  swapDevices = [ ];

  # === Importar módulos compartilhados ===
  imports = [
    ../modules/permission-manager.nix
    ../modules/agent-orchestrator.nix
    ../modules/tupi-desktop.nix
    ../modules/tupi-classic-desktop.nix
    ../modules/ai-router.nix
    ../modules/tupi-assistant.nix
    ../modules/tupi-agent.nix
    ./vm.nix   # só vale para a VM de teste (nixos-rebuild build-vm / system.build.vm)
  ]
  # trechos de configuração NixOS que a IA ensaiou no ST e você aprovou
  ++ lib.optional (builtins.pathExists ./agent-extra.nix) ./agent-extra.nix;

  # === IA como agente: RESTRITA por padrão ===
  # Mudar: app → "Permissões da IA", `tupi-agent policy`, ou aqui (tupi.agent.mode /
  # tupi.agent.policyOverrides). Mudanças de sistema pedidas à IA são ENSAIADAS no ST (container com
  # esta mesma configuração), mostradas como diff e só aplicadas aqui com a sua aprovação, com ponto
  # de retorno (geração anterior + snapshot Btrfs). Ficam em ./agent-config.json e ./agent-extra.nix.
  tupi.agent.enable = true;

  # === ST: ambiente de ensaio (container sob demanda, sem rede e sem o seu /home) ===
  tupi.st.enable = true;

  # === IA por API (sobe no boot em 127.0.0.1:8080) ===
  # Depois de instalar: menu Iniciar → "Configurar IA" (ou `tupi-ai-setup`).
  tupi.aiRouter.enable = true;
  # App do Pajé, a IA do TUPIOS (estilo ChatGPT) + bolinha flutuante sempre visível
  tupi.assistant.enable = true;

  # === Permission Manager (Fase 5) ===
  tupi.permissionManager.enable = true;

  # === Agent Orchestrator (Fase 7) ===
  tupi.agentOrchestrator.enable = true;

  # === Área de trabalho ===
  # Padrão: estilo Windows (XFCE, barra embaixo, menu Iniciar) — o mesmo da ISO.
  # O desktop nativo Sway (Fase 8) continua disponível: troque os dois valores
  # abaixo. Os dois juntos não: o tupi.desktop força variáveis Wayland
  # (GDK_BACKEND=wayland etc.) que quebram os apps na sessão X11 do XFCE.
  tupi.classicDesktop.enable = true;
  tupi.desktop.enable = false;

  # === Segurança Básica (Fase 1) ===
  security.apparmor.enable = true;
  security.sudo.enable = true;

  # Firewall básico
  networking.firewall.enable = true;
  networking.firewall.allowedTCPPorts = [ ];
  networking.firewall.allowedUDPPorts = [ ];

  # === TUPIOS Metadata ===
  environment.etc."tupi/version".text = "0.1.0-alpha";
  environment.etc."tupi/core".text = "pd";

  # === Validação da Fase 1 ===
  # Critérios de aceite:
  # ✅ Flake compila com `nix flake check`
  # ✅ VM boota em QEMU com GUI funcional (Sway)
  # ✅ System Monitor (btop) exibe CPU/RAM/storage/processos
  # ✅ Dados do usuário em subvolume separado (@home)
  # ✅ Zero conexões externas não solicitadas
  # ✅ Apenas pacotes essenciais instalados
}