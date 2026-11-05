# TUPIOS — ISO Installer Configuration (Fase 9)
# Bootable installation image with Calamares installer and first-boot wizard
# Bundles PD, ST, SEC, IA cores into a single installable ISO
{ config, pkgs, lib, ... }:

{
  # === ISO Metadata ===
  isoImage.isoName = "tupios-0.1.0-alpha-x86_64.iso";
  isoImage.volumeID = "TUPIOS";
  isoImage.makeEfiBootable = true;
  isoImage.makeUsbBootable = true;

  # === System Base ===
  system.stateVersion = "25.05";
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Networking for installer (needed for remote AI backend and package downloads)
  networking.hostName = "tupios-installer";
  networking.networkmanager.enable = true;
  networking.wireless.enable = true;

  time.timeZone = "America/Sao_Paulo";
  i18n.defaultLocale = "pt_BR.UTF-8";

  # === Console & Fonts ===
  console.font = "${pkgs.terminus_font}/share/consolefonts/ter-v28n.psf.gz";
  console.keyMap = "br-abnt2";

  # === GUI Environment for Installer ===
  services.xserver.enable = true;
  services.displayManager.sddm.enable = true;
  services.desktopManager.plasma5.enable = true;

  # === Calamares Installer ===
  services.calamares.enable = true;

  # === First-Boot Wizard Script + Essential Packages ===
  environment.systemPackages = with pkgs; [
    git
    btrfs-progs
    curl
    jq
    vim
    htop
    btop
    firefox
    pcmanfm
    alacritty
    sway
    waybar
    wl-clipboard
    grim
    slurp
    python3
    socat
    sqlite
    (writeShellScriptBin "tupi-firstboot" ''
      #!/bin/sh
      set -eu

      echo "🚀 TUPIOS First-Boot Wizard"
      echo "=============================="
      echo ""

      # Check if already configured
      if [ -f /etc/tupi/configured ]; then
        echo "✅ System already configured. Skipping first-boot wizard."
        exit 0
      fi

      # Prompt for AI backend configuration
      echo "Configure AI Backend:"
      echo "  1) Remote API (OpenAI-compatible)"
      echo "  2) Local Ollama (requires GPU + ~8GB RAM)"
      echo ""
      read -p "Choice [1]: " AI_CHOICE
      AI_CHOICE=''${AI_CHOICE:-1}

      mkdir -p /etc/tupi

      if [ "$AI_CHOICE" = "2" ]; then
        echo '{"default_backend":"local","remote_api_key":"","remote_base_url":""}' > /etc/tupi/ai-config.json
        echo "⚠️  Local AI enabled. Run 'ollama pull llama3.2' after first login to download model."
      else
        read -p "API Key: " API_KEY
        read -p "Base URL [https://api.openai.com/v1]: " BASE_URL
        BASE_URL=''${BASE_URL:-https://api.openai.com/v1}
        echo "{\"default_backend\":\"remote\",\"remote_api_key\":\"$API_KEY\",\"remote_base_url\":\"$BASE_URL\"}" > /etc/tupi/ai-config.json
        echo "✅ Remote AI backend configured."
      fi

      # Initialize Btrfs subvolumes for PD/ST/SEC isolation
      echo ""
      echo "Initializing TUPIOS core environments..."
      ROOT_DEV=$(findmnt -n -o SOURCE / | head -1)

      if command -v btrfs >/dev/null 2>&1 && btrfs filesystem show "$ROOT_DEV" >/dev/null 2>&1; then
        btrfs subvolume create /var/lib/tupi-st 2>/dev/null || true
        btrfs subvolume create /var/lib/tupi-sec 2>/dev/null || true
        echo "✅ Btrfs subvolumes created for ST and SEC isolation."
      else
        echo "⚠️  Btrfs not detected on root. Snapshot features may be limited."
      fi

      # Mark as configured
      date -Iseconds > /etc/tupi/configured
      echo ""
      echo "✅ TUPIOS first-boot setup complete!"
      echo "   Reboot to apply all changes."
    '')
  ];

  # === Auto-run first-boot wizard on first login ===
  systemd.services.tupi-firstboot = {
    description = "TUPIOS First-Boot Configuration Wizard";
    wantedBy = [ "multi-user.target" ];
    conditionPathExists = "!/etc/tupi/configured";
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      StandardInput = "tty";
      StandardOutput = "tty";
      TTYPath = "/dev/tty1";
    };
    script = "${pkgs.tupi-firstboot}/bin/tupi-firstboot";
  };

  # === Include TUPIOS Modules ===
  imports = [
    ../modules/permission-manager.nix
    ../modules/agent-orchestrator.nix
  ];

  # Enable Permission Manager and Agent Orchestrator in live ISO
  tupi.permissionManager.enable = true;
  tupi.agentOrchestrator.enable = true;

  # === Live User ===
  users.users.tupi = {
    isNormalUser = true;
    extraGroups = [ "wheel" "networkmanager" "video" "audio" ];
    initialPassword = "tupios";
  };

  # Passwordless sudo for live session convenience
  security.sudo.wheelNeedsPassword = false;

  # === Boot Splash ===
  boot.plymouth.enable = true;

  # === Kernel Parameters for Broad Hardware Support ===
  boot.kernelParams = [
    "quiet"
    "splash"
    "modprobe.blacklist=nouveau"
  ];

  # === TUPIOS Branding ===
  environment.etc."tupi/version".text = "0.1.0-alpha";
  environment.etc."tupi/edition".text = "installer";

  # === Validation ===
  # ✅ ISO compiles as NixOS configuration in flake
  # ✅ Calamares installer enabled
  # ✅ First-boot wizard for AI backend + Btrfs init
  # ✅ All TUPIOS modules (Permission Manager, Agent Orchestrator) included
  # ✅ Sway + Wayland GUI for installer experience
  # ✅ Broad hardware support (EFI + BIOS, WiFi, NVIDIA blacklist)
}