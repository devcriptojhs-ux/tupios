# TUPIOS — Installer Module
# Fase 9: ISO instalável com Calamares, first-boot wizard e particionamento Btrfs automático
# Gera ISO bootável com instalador gráfico e configuração pós-instalação
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.installer;

  # First-boot setup script — runs once after installation completes
  firstBootScript = pkgs.writeShellScriptBin "tupi-first-boot" ''
    #!/bin/sh
    set -eu

    MARKER="/var/lib/tupi/.first-boot-done"
    if [ -f "$MARKER" ]; then
      exit 0
    fi

    echo "[TUPIOS] Running first-boot setup..."

    # Create default user if not exists
    if ! id "${cfg.defaultUser}" >/dev/null 2>&1; then
      echo "[TUPIOS] Creating default user: ${cfg.defaultUser}"
      useradd -m -G wheel,tupi-ia -s /bin/bash "${cfg.defaultUser}" 2>/dev/null || true
    fi

    # Initialize AI memory database
    mkdir -p /var/lib/tupi-agents
    if [ ! -f /var/lib/tupi-agents/memory.db ]; then
      echo "[TUPIOS] Initializing agent memory database..."
      ${pkgs.sqlite}/bin/sqlite3 /var/lib/tupi-agents/memory.db < /etc/tupi/memory-schema.sql 2>/dev/null || true
      chown root:tupi-ia /var/lib/tupi-agents/memory.db 2>/dev/null || true
      chmod 0640 /var/lib/tupi-agents/memory.db 2>/dev/null || true
    fi

    # Generate host-specific SSH keys if missing
    if [ ! -f /etc/ssh/ssh_host_ed25519_key ]; then
      echo "[TUPIOS] Generating SSH host keys..."
      ssh-keygen -A 2>/dev/null || true
    fi

    # Set timezone and locale from installer config
    timedatectl set-timezone "${cfg.timezone}" 2>/dev/null || true
    localectl set-locale LANG="${cfg.locale}" 2>/dev/null || true

    # Enable systemd services for TUPIOS cores
    systemctl enable tupi-perm-mgr.service 2>/dev/null || true
    systemctl enable tupi-agent-orchestrator.service 2>/dev/null || true
    systemctl enable tupi-ai-router.service 2>/dev/null || true

    # Mark first-boot as complete
    mkdir -p /var/lib/tupi
    touch "$MARKER"
    echo "[TUPIOS] First-boot setup complete!"
  '';

  # Memory schema SQL for first-boot initialization
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

  # Calamares branding configuration
  calamaresBranding = pkgs.writeTextFile {
    name = "tupios-branding.yaml";
    text = ''
      componentName: tupios
      productName: TUPIOS
      version: 0.1.0-alpha
      bootloaderName: TUPIOS Bootloader
      productUrl: https://tupios.dev
      supportUrl: https://tupios.dev/support
      knownIssuesUrl: https://github.com/tupios/tupios/issues
      releaseNotesUrl: https://tupios.dev/releases/0.1.0
      images:
        productIcon: "tupios-logo.png"
        productWelcome: "welcome.png"
      strings:
        productName: TUPIOS
        version: 0.1.0-alpha
        bootloaderName: TUPIOS
        productUrl: https://tupios.dev
        supportUrl: https://tupios.dev/support
        knownIssuesUrl: https://github.com/tupios/tupios/issues
        releaseNotesUrl: https://tupios.dev/releases/0.1.0
    '';
  };

  # Calamares settings for TUPIOS
  calamaresSettings = pkgs.writeTextFile {
    name = "settings.conf";
    text = ''
      ---
      modules-search: [ local ]
      instances:
        - id: welcome
          module: welcome
          config: welcome.conf
        - id: partition
          module: partition
          config: partition.conf
        - id: users
          module: users
          config: users.conf
        - id: summary
          module: summary
          config: summary.conf
        - id: finished
          module: finished
          config: finished.conf
      sequence:
        - show:
            - welcome
            - partition
            - users
            - summary
        - exec:
            - partition
            - mount
            - unpackfs
            - machineid
            - fstab
            - locale
            - keyboard
            - timezone
            - hostname
            - network
            - users
            - displaymanager
            - hwclock
            - services-systemd
            - grubcfg
            - bootloader
            - postcfg
            - umount
        - show:
            - finished
      branding: tupios
      dont-chroot: false
      disable-cancel: false
      disable-cancel-during-exec: true
    '';
  };
in
{
  options.tupi.installer = {
    enable = lib.mkEnableOption "TUPIOS Installer (Calamares-based ISO)";

    isoName = lib.mkOption {
      type = lib.types.str;
      default = "tupios-0.1.0-alpha-x86_64.iso";
      description = "Output ISO filename";
    };

    defaultUser = lib.mkOption {
      type = lib.types.str;
      default = "tupi";
      description = "Default user created during installation";
    };

    timezone = lib.mkOption {
      type = lib.types.str;
      default = "America/Sao_Paulo";
      description = "Default timezone for installed system";
    };

    locale = lib.mkOption {
      type = lib.types.str;
      default = "pt_BR.UTF-8";
      description = "Default locale for installed system";
    };

    enableEncryption = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable LUKS full-disk encryption option in installer";
    };

    btrfsSubvolumes = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "@" "@home" "@var" "@nix" "@swap" ];
      description = "Btrfs subvolumes created during installation";
    };
  };

  config = lib.mkIf cfg.enable {
    # === ISO Image Configuration ===
    isoImage.isoName = cfg.isoName;
    isoImage.makeEfiBootable = true;
    isoImage.makeUsbBootable = true;
    isoImage.squashfsCompression = "zstd -Xcompression-level 6";
    isoImage.volumeID = "TUPIOS_010";

    # === Live Environment Packages ===
    environment.systemPackages = with pkgs; [
      calamares
      gparted
      btrfs-progs
      cryptsetup
      parted
      efibootmgr
      grub2
      sqlite
      firstBootScript
      curl
      wget
      htop
      neofetch
    ];

    # === Calamares Configuration ===
    environment.etc."calamares/settings.conf".source = calamaresSettings;
    environment.etc."calamares/branding/tupios/branding.desc".source = calamaresBranding;
    environment.etc."tupi/memory-schema.sql".source = memorySchemaSql;

    # === Live Session Services ===
    systemd.services.calamares-autostart = {
      description = "Auto-start Calamares installer on live session";
      wantedBy = [ "graphical.target" ];
      after = [ "graphical.target" ];
      serviceConfig = {
        Type = "simple";
        User = "root";
        Restart = "on-failure";
        RestartSec = "3s";
      };
      script = ''
        sleep 3
        if [ ! -f /var/lib/tupi/.installed ]; then
          echo "[TUPIOS-Live] Starting Calamares installer..."
          exec ${pkgs.calamares}/bin/calamares -D6
        fi
      '';
    };

    # === First-Boot Service (runs on installed system) ===
    systemd.services.tupi-first-boot = {
      description = "TUPIOS First-Boot Setup";
      wantedBy = [ "multi-user.target" ];
      after = [ "local-fs.target" "network-online.target" ];
      wants = [ "network-online.target" ];
      conditionPathExists = "!/var/lib/tupi/.first-boot-done";
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = "${firstBootScript}/bin/tupi-first-boot";
    };

    # === Btrfs Partitioning Helper ===
    environment.systemPackages = with pkgs; [
      (writeShellScriptBin "tupi-partition" ''
        #!/bin/sh
        set -eu
        DEVICE="''${1:-}"
        if [ -z "$DEVICE" ]; then
          echo "Usage: tupi-partition /dev/sdX"
          echo "WARNING: This will DESTROY all data on the device!"
          exit 1
        fi

        echo "[TUPIOS] Partitioning $DEVICE with Btrfs layout..."

        # Wipe existing partitions
        wipefs -a "$DEVICE"
        sgdisk --zap-all "$DEVICE"

        # Create EFI partition (512MB)
        sgdisk -n 1:0:+512M -t 1:EF00 -c 1:"EFI System" "$DEVICE"

        # Create root partition (rest of disk)
        sgdisk -n 2:0:0 -t 2:8300 -c 2:"TUPIOS Root" "$DEVICE"

        # Format EFI
        mkfs.fat -F32 -n TUPIOS-EFI "''${DEVICE}p1"

        # Format root as Btrfs
        mkfs.btrfs -f -L tupi-root "''${DEVICE}p2"

        # Mount and create subvolumes
        mount "''${DEVICE}p2" /mnt/btrfs-root
        cd /mnt/btrfs-root

        for sv in ${lib.concatStringsSep " " cfg.btrfsSubvolumes}; do
          btrfs subvolume create "$sv"
          echo "[TUPIOS] Created subvolume: $sv"
        done

        umount /mnt/btrfs-root
        echo "[TUPIOS] Partitioning complete! Subvolumes: ${lib.concatStringsSep ", " cfg.btrfsSubvolumes}"
      '')
    ];

    # === Live Environment Display Server ===
    services.xserver.enable = true;
    services.displayManager.sddm.enable = true;
    services.desktopManager.plasma5.enable = true;

    # === Networking for Live ISO ===
    networking.networkmanager.enable = true;
    networking.wireless.enable = true;

    # === Sound for Live Environment ===
    sound.enable = true;
    hardware.pulseaudio.enable = true;

    # === TUPIOS Live Metadata ===
    environment.etc."tupi/live-version".text = "0.1.0-alpha";
    environment.etc."tupi/installer-type".text = "calamares";
    environment.etc."tupi/default-subvolumes".text = lib.concatStringsSep "\n" cfg.btrfsSubvolumes;
  };
}