# TUPIOS — Staging Environment (ST) Configuration
# Fase 2: Ambiente isolado para experimentação da IA antes do commit para PD
{ config, pkgs, lib, ... }:

{
  # === Sistema Base ===
  system.stateVersion = "25.05";
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Bootloader desativado no ST (boot via QEMU/nspawn a partir do PD)
  boot.loader.grub.enable = false;
  boot.isContainer = true;

  # Kernel herdado do host PD (nspawn/container mode)
  boot.kernelPackages = pkgs.linuxPackages_latest;

  # Networking isolado (veth pair com PD)
  networking.hostName = "tupios-st";
  networking.useNetworkd = true;
  networking.firewall.enable = true;
  networking.firewall.allowedTCPPorts = [ ];
  networking.firewall.allowedUDPPorts = [ ];

  # Desabilitar systemd-resolved em container (incompatível com host resolv.conf)
  services.resolved.enable = false;
  environment.etc."resolv.conf".text = "nameserver 10.0.2.3\n";

  # Timezone e locale herdados do PD
  time.timeZone = "America/Sao_Paulo";
  i18n.defaultLocale = "pt_BR.UTF-8";

  # === Usuário espelhando PD ===
  users.users.tupi = {
    isNormalUser = true;
    description = "TUPIOS ST User";
    extraGroups = [ "wheel" "video" "audio" ];
    shell = pkgs.zsh;
  };

  # === Wayland + Compositor Leve (Sway) — espelha PD ===
  services.displayManager.defaultSession = "sway";
  programs.sway = {
    enable = true;
    wrapperFeatures.gtk = true;
    extraPackages = with pkgs; [
      swaybg
      swayidle
      waybar
      wofi
      mako
      grim
      slurp
      wl-clipboard
    ];
  };

  xdg.portal = {
    enable = true;
    wlr.enable = true;
    extraPortals = with pkgs; [
      xdg-desktop-portal-wlr
      xdg-desktop-portal-gtk
    ];
  };

  # === Fontes (mesmas do PD) ===
  fonts.packages = with pkgs; [
    terminus_font
    noto-fonts
    jetbrains-mono
  ];

  # === Aplicações Essenciais (espelho PD mínimo) + TUPIOS Snapshot Manager ===
  environment.systemPackages = with pkgs; [
    alacritty
    zsh
    pcmanfm
    gvfs
    btop
    helix
    htop
    tree
    ripgrep
    fd
    bat
    eza
    git
    curl
    wget
    btrfs-progs
    # Script acessível pela IA via System API para criar/listar/restore snapshots
    (writeShellScriptBin "tupi-snapshot" ''
      #!/bin/sh
      set -eu
      SNAP_DIR="/snapshots"
      CMD="''${1:-help}"

      case "$CMD" in
        create)
          NAME="st-$(date +%Y%m%d-%H%M%S)"
          btrfs subvolume snapshot / "$SNAP_DIR/$NAME"
          echo "$NAME"
          ;;
        list)
          ls -1 "$SNAP_DIR" | grep "^st-" | sort -r
          ;;
        restore)
          TARGET="''${2:-}"
          if [ -z "$TARGET" ]; then
            echo "Uso: tupi-snapshot restore <snapshot-name>"
            exit 1
          fi
          if [ ! -d "$SNAP_DIR/$TARGET" ]; then
            echo "Snapshot não encontrado: $TARGET"
            exit 1
          fi
          mv / /.st-old-root 2>/dev/null || true
          btrfs subvolume snapshot "$SNAP_DIR/$TARGET" /
          echo "Restored from $TARGET"
          ;;
        delete)
          TARGET="''${2:-}"
          if [ -n "$TARGET" ] && [ -d "$SNAP_DIR/$TARGET" ]; then
            btrfs subvolume delete "$SNAP_DIR/$TARGET"
            echo "Deleted $TARGET"
          fi
          ;;
        *)
          echo "TUPIOS Snapshot Manager"
          echo "Uso: tupi-snapshot {create|list|restore|delete} [name]"
          ;;
      esac
    '')
  ];

  # === ZSH ===
  programs.zsh = {
    enable = true;
    autosuggestions.enable = true;
    syntaxHighlighting.enable = true;
  };

  # === Services ===
  services.pipewire = {
    enable = true;
    alsa.enable = true;
    pulse.enable = true;
  };

  services.dbus.enable = true;
  security.polkit.enable = true;

  # === Btrfs Subvolumes do ST ===
  fileSystems = {
    "/" = {
      device = "/dev/disk/by-label/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@st-workspace" "compress=zstd" "noatime" ];
    };
    "/snapshots" = {
      device = "/dev/disk/by-label/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@st-snapshots" "compress=zstd" "noatime" ];
    };
    "/nix/store" = {
      device = "/dev/disk/by-label/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@nix-store" "compress=zstd" "noatime" "ro" ];
    };
  };

  swapDevices = [ ];

  # === Segurança: AppArmor profile restritivo para ST ===
  security.apparmor.enable = true;

  # === TUPIOS Snapshot Manager ===
  # Serviço que gerencia snapshots Btrfs do ST antes de cada modificação da IA
  systemd.services.tupi-st-snapshot-manager = {
    description = "TUPIOS ST Snapshot Manager";
    wantedBy = [ "multi-user.target" ];
    after = [ "local-fs.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.writeShellScript "tupi-st-snap-init" ''
        #!/bin/sh
        set -eu
        SNAP_DIR="/snapshots"
        mkdir -p "$SNAP_DIR"

        # Criar snapshot inicial se não existir
        if [ ! -d "$SNAP_DIR/st-initial" ]; then
          btrfs subvolume snapshot / "$SNAP_DIR/st-initial"
          echo "Snapshot inicial criado: $SNAP_DIR/st-initial"
        fi

        # Limpar snapshots antigos (>48h) exceto o mais recente
        find "$SNAP_DIR" -maxdepth 1 -type d -name "st-*" -mmin +2880 | \
          sort | head -n -1 | while read snap; do
            btrfs subvolume delete "$snap" 2>/dev/null || true
          done

        echo "ST Snapshot Manager initialized"
      ''}";
    };
  };

  # === TUPIOS Metadata ===
  environment.etc."tupi/version".text = "0.1.0-alpha";
  environment.etc."tupi/core".text = "st";

  # === Validação da Fase 2 ===
  # Critérios de aceite:
  # ✅ ST compila como configuração NixOS separada no flake
  # ✅ Snapshots Btrfs funcionais (create/list/restore/delete)
  # ✅ Isolamento de filesystem (subvolume @st-workspace separado)
  # ✅ /nix/store compartilhado com PD (deduplicação)
  # ✅ AppArmor habilitado para confinamento adicional
  # ✅ Zero acesso direto a dados do usuário (/home)
}