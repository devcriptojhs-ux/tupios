# TUPIOS — Production Domain (PD) Configuration
# Fase 1: NixOS Personalizado com PD Funcional
{ config, pkgs, lib, ... }:

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
    extraGroups = [ "wheel" "networkmanager" "video" "audio" ];
    shell = pkgs.zsh;
  };

  # === Wayland + Compositor Leve (Sway) ===
  services.displayManager.defaultSession = "sway";
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
  ];

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
  # criou os subvolumes corretamente.

  fileSystems = {
    "/" = {
      device = "/dev/disk/by-label/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@pd-root" "compress=zstd" "noatime" ];
    };
    "/home" = {
      device = "/dev/disk/by-label/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@home" "compress=zstd" "noatime" ];
    };
    "/nix/store" = {
      device = "/dev/disk/by-label/tupi-root";
      fsType = "btrfs";
      options = [ "subvol=@nix-store" "compress=zstd" "noatime" "ro" ];
    };
    "/boot" = {
      device = "/dev/disk/by-label/EFI";
      fsType = "vfat";
    };
  };

  swapDevices = [ ];

  # === Importar módulos compartilhados ===
  imports = [ ../modules/permission-manager.nix ];

  # === Permission Manager (Fase 5) ===
  tupi.permissionManager.enable = true;

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