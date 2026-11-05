# TUPIOS — Native Desktop Module
# Fase 8: Desktop TUPIOS em Rust + Smithay (Wayland compositor nativo)
# Substitui Sway como compositor Wayland próprio com integração IA nativa
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.desktop;

  # Placeholder Rust compositor binary — will be replaced by real build in later iteration
  tupiCompositor = pkgs.writeShellScriptBin "tupi-compositor" ''
    #!/bin/sh
    echo "[TUPI-Desktop] Starting native Wayland compositor..."
    echo "[TUPI-Desktop] Backend: ${cfg.backend}"
    echo "[TUPI-Desktop] Scale: ${toString cfg.scale}"
    echo "[TUPI-Desktop] Theme: ${cfg.theme}"

    # Launch smithay-based compositor (stub for Phase 8 scaffold)
    # Real implementation will use smithay + winit + egui for panels
    exec ${pkgs.sway}/bin/sway --config /etc/tupi/desktop-sway-fallback.conf
  '';

  # Fallback sway config used until native compositor is fully implemented
  swayFallbackConfig = pkgs.writeTextFile {
    name = "desktop-sway-fallback.conf";
    text = ''
      # TUPIOS Desktop — Sway fallback config (Phase 8 transitional)
      # Will be replaced by native Rust compositor in subsequent iterations

      set $mod Mod4
      font pango JetBrains Mono 12

      # Colors matching TUPIOS identity (#52EA23 primary)
      client.focused          #52EA23 #1a3d1a #ffffff #52EA23 #52EA23
      client.focused_inactive #333333 #111111 #aaaaaa #333333 #333333
      client.unfocused        #222222 #000000 #666666 #222222 #222222
      client.urgent           #FF4444 #3d1a1a #ffffff #FF4444 #FF4444

      output * bg #000000 solid_color

      # Keybindings
      bindsym $mod+Return exec ${pkgs.alacritty}/bin/alacritty
      bindsym $mod+d exec ${pkgs.wofi}/bin/wofi --show drun
      bindsym $mod+Shift+q kill
      bindsym $mod+Shift+e exec swaymsg exit

      # Workspace navigation
      bindsym $mod+1 workspace number 1
      bindsym $mod+2 workspace number 2
      bindsym $mod+3 workspace number 3
      bindsym $mod+4 workspace number 4

      # Window management
      bindsym $mod+h focus left
      bindsym $mod+j focus down
      bindsym $mod+k focus up
      bindsym $mod+l focus right
      bindsym $mod+Shift+h move left
      bindsym $mod+Shift+j move down
      bindsym $mod+Shift+k move up
      bindsym $mod+Shift+l move right

      # Layout
      bindsym $mod+e layout toggle split
      bindsym $mod+f fullscreen toggle
      bindsym $mod+space floating toggle

      # Status bar placeholder (will be replaced by native panel)
      bar {
        position top
        colors {
          background #111111
          statusline #52EA23
          separator #333333
          focused_workspace #52EA23 #1a3d1a #ffffff
          active_workspace #333333 #111111 #aaaaaa
          inactive_workspace #222222 #000000 #666666
        }
        font pango JetBrains Mono 11
        status_command while true; do echo "🟢 TUPIOS Desktop | $(date '+%H:%M')"; sleep 5; done
      }

      # Startup
      exec_always ${pkgs.dbus}/bin/dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP
    '';
  };
in
{
  options.tupi.desktop = {
    enable = lib.mkEnableOption "TUPIOS Native Desktop";

    backend = lib.mkOption {
      type = lib.types.enum [ "wayland" "x11-fallback" ];
      default = "wayland";
      description = "Display server backend";
    };

    scale = lib.mkOption {
      type = lib.types.float;
      default = 1.0;
      description = "Display scaling factor";
    };

    theme = lib.mkOption {
      type = lib.types.str;
      default = "tupi-dark";
      description = "Desktop theme name";
    };

    terminal = lib.mkOption {
      type = lib.types.str;
      default = "${pkgs.alacritty}/bin/alacritty";
      description = "Default terminal emulator";
    };

    launcher = lib.mkOption {
      type = lib.types.str;
      default = "${pkgs.wofi}/bin/wofi --show drun";
      description = "Application launcher command";
    };

    wallpaper = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Wallpaper image path (null = solid black)";
    };

    autoStartAgents = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Auto-start AI agents on desktop login";
    };
  };

  config = lib.mkIf cfg.enable {
    # === Display Server & Compositor ===
    services.xserver.enable = false;
    programs.sway.enable = true;
    programs.sway.extraSessionCommands = ''
      export XDG_SESSION_TYPE=wayland
      export XDG_CURRENT_DESKTOP=TUPIOS
      export TUPI_DESKTOP_BACKEND=${cfg.backend}
      export TUPI_DESKTOP_SCALE=${toString cfg.scale}
      export TUPI_DESKTOP_THEME=${cfg.theme}
    '';

    # Install fallback sway config
    environment.etc."tupi/desktop-sway-fallback.conf".source = swayFallbackConfig;

    # === Core Desktop Packages ===
    environment.systemPackages = with pkgs; [
      tupiCompositor
      alacritty
      wofi
      waybar
      mako
      slurp
      grim
      wl-clipboard
      brightnessctl
      pamixer
      playerctl
      dbus
      xdg-utils
    ] ++ lib.optionals (cfg.wallpaper != null) [ swaybg ];

    # === Environment Variables ===
    environment.variables = {
      XDG_SESSION_TYPE = "wayland";
      XDG_CURRENT_DESKTOP = "TUPIOS";
      MOZ_ENABLE_WAYLAND = "1";
      QT_QPA_PLATFORM = "wayland";
      SDL_VIDEODRIVER = "wayland";
      CLUTTER_BACKEND = "wayland";
      GDK_BACKEND = "wayland";
    };

    # === Font Configuration ===
    fonts.packages = with pkgs; [
      jetbrains-mono
      noto-fonts
      noto-fonts-cjk-sans
      font-awesome
    ];

    # === Portal & D-Bus Services ===
    xdg.portal = {
      enable = true;
      wlr.enable = true;
      extraPortals = with pkgs; [
        xdg-desktop-portal-wlr
        xdg-desktop-portal-gtk
      ];
    };

    services.dbus.enable = true;
    security.polkit.enable = true;

    # === Session Target ===
    systemd.user.targets.tupi-desktop = {
      description = "TUPIOS Desktop Session";
      wants = [ "graphical-session.target" ];
      after = [ "graphical-session-pre.target" ];
    };

    # === Auto-start AI Agents (optional) ===
    systemd.user.services.tupi-agents-autostart = lib.mkIf cfg.autoStartAgents {
      description = "Auto-start TUPIOS AI agents on desktop login";
      wantedBy = [ "tupi-desktop.target" ];
      after = [ "tupi-agent-orchestrator.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        echo "[TUPI-Desktop] Auto-starting AI agents..."
        # Agent startup will be handled by Agent Orchestrator MCP protocol
        # This service signals that the desktop session is ready for agents
        echo "[TUPI-Desktop] Desktop session ready for agent connections"
      '';
    };

    # === TUPIOS Desktop Metadata ===
    environment.etc."tupi/desktop-version".text = "0.1.0-alpha";
    environment.etc."tupi/desktop-backend".text = cfg.backend;
  };
}