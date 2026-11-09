# TUPIOS — ISO Installer Configuration (Fase 9)
# Bootable live/installation image with first-boot wizard
# Bundles PD, ST, SEC, IA cores into a single installable ISO
{ config, pkgs, lib, ... }:

let
  tupiFirstboot = pkgs.writeShellScriptBin "tupi-firstboot" ''
    set -eu

    if [ "$(id -u)" -ne 0 ]; then
      echo "Execute como root: sudo tupi-firstboot"
      exit 1
    fi

    echo "🚀 TUPIOS First-Boot Wizard"
    echo "=============================="
    echo ""

    # Check if already configured
    if [ -f /etc/tupi/configured ]; then
      echo "✅ System already configured. Skipping first-boot wizard."
      echo "   Para reconfigurar só a IA: sudo tupi-ai-setup"
      exit 0
    fi

    # Backend de IA (chave da API / modelo) + teste de conexão — módulo ai-router.nix
    tupi-ai-setup || echo "⚠️  IA não configurada agora. Rode 'sudo tupi-ai-setup' quando quiser."

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
  '';
in
{
  # === Include TUPIOS Modules ===
  imports = [
    ../modules/permission-manager.nix
    ../modules/agent-orchestrator.nix
    ../modules/ai-router.nix
    ../modules/tupi-classic-desktop.nix
    ../modules/tupi-assistant.nix
    ../modules/tupi-agent.nix
  ];

  # === ISO Metadata ===
  # No NixOS 25.05 `isoImage.isoName` virou `image.fileName` e o nome real do
  # arquivo vem de `image.baseName` (o iso-image.nix define esse valor, por isso o mkForce).
  image.baseName = lib.mkForce "tupios-0.1.0-alpha-x86_64";
  isoImage.volumeID = "TUPIOS";
  isoImage.makeEfiBootable = true;
  isoImage.makeUsbBootable = true;

  # === System Base ===
  system.stateVersion = "25.05";
  nix.settings.experimental-features = [ "nix-command" "flakes" ];

  # Networking for installer (needed for remote AI backend and package downloads)
  # Cabo/NAT da VM: DHCP automático. Wi-Fi: `sudo systemctl start wpa_supplicant` + `wpa_cli`.
  networking.hostName = "tupios-installer";
  networking.networkmanager.enable = false;
  networking.wireless.enable = lib.mkForce true;

  time.timeZone = "America/Sao_Paulo";
  i18n.defaultLocale = "pt_BR.UTF-8";

  # === Console & Fonts ===
  console.font = "${pkgs.terminus_font}/share/consolefonts/ter-v28n.psf.gz";
  console.keyMap = "br-abnt2";

  # === Interface gráfica: área de trabalho estilo Windows (XFCE) ===
  # Boot direto na área de trabalho, sem tela de login. Na 1ª entrada abre
  # sozinho o assistente "Configurar IA" (provedor, chave e lista de modelos).
  tupi.classicDesktop = {
    enable = true;
    autoLoginUser = "nixos";
  };
  # Sem bloqueio de tela no live: o usuário "nixos" não tem senha e o PAM do xfce4-screensaver não aceita
  # senha vazia — depois de alguns minutos parado a tela travava sem ter como desbloquear.
  services.xserver.desktopManager.xfce.enableScreensaver = false;

  # === Máquinas virtuais ===
  # Guest additions do VirtualBox: copiar/colar entre Windows e a VM e ajuste
  # automático da resolução da janela.
  #
  # O serviço `virtualbox` do NixOS exige /dev/vboxguest e, em máquina real,
  # seguraria o boot por 90s esperando esse dispositivo. Por isso ele não fica
  # mais preso ao multi-user.target: o udev só o inicia quando /dev/vboxguest
  # aparece (ou seja, apenas dentro do VirtualBox).
  virtualisation.virtualbox.guest.enable = true;
  systemd.services.virtualbox.wantedBy = lib.mkForce [ ];
  services.udev.extraRules = ''
    SUBSYSTEM=="misc", KERNEL=="vboxguest", TAG+="systemd", ENV{SYSTEMD_WANTS}+="virtualbox.service"
  '';
  # Nota: o vmwgfx NÃO pode ficar em blacklist. No VirtualBox com controlador
  # VMSVGA (padrão) e no VMware ele é o driver de vídeo. As 3 linhas
  # "vmwgfx ... unsupported hypervisor" no VirtualBox são apenas avisos.

  # === Pacotes de linha de comando ===
  environment.systemPackages = with pkgs; [
    git
    btrfs-progs
    curl
    jq
    vim
    htop
    btop
    python3
    socat
    sqlite
    tupiFirstboot
  ];

  # Texto mostrado nos consoles de texto (Ctrl+Alt+F2...). A interface gráfica
  # abre sozinha no boot.
  services.getty.helpLine = ''

    ==================== TUPIOS 0.1.0-alpha (live) ====================
    A área de trabalho abre sozinha. Voltar para ela: Ctrl+Alt+F7
    Configurar a IA:  tupi-ai-setup   |   Perguntar:  tupi-ask "sua pergunta"
    IA agente:  tupi-assistant --send "pedido"  |  tupi-agent status / pending / stop
    Pajé e subagentes:  paje agentes / trabalhos / harness   (docs: /etc/tupi/paje/HARNESS.md)
    Serviços TUPIOS:  systemctl status tupi-ai-router tupi-perm-mgr tupi-agent-exec tupi-agent-orchestrator
    Usuário live alternativo:  tupi / senha: tupios
  '';

  # Enable TUPIOS services in live ISO
  tupi.permissionManager.enable = true;
  tupi.agentOrchestrator.enable = true;
  # IA por API: sobe no boot (127.0.0.1:8080); só precisa da chave via tupi-ai-setup
  tupi.aiRouter.enable = true;
  # App do Pajé, a IA do TUPIOS (estilo ChatGPT) + bolinha flutuante sempre visível
  tupi.assistant.enable = true;
  # IA como agente, RESTRITA por padrão (só leitura liberada; o resto pergunta ou é negado).
  # Mudar: tela "Permissões da IA" no app, `tupi-agent policy`, ou tupi.agent.mode aqui.
  tupi.agent.enable = true;
  # Live: não existe ST nem configuração no disco. A IA explica isso e oferece mudanças temporárias
  # (set_kernel_param, que some ao reiniciar).
  tupi.agent.liveSystem = true;

  # === Grupos necessários para os módulos TUPIOS ===
  # (tupi-ia também é declarado pelos próprios módulos; declarar aqui é inofensivo)
  users.groups.tupi-ia = { };
  users.groups.tupi-st = { };
  users.groups.tupi-sec = { };

  # === Live User ===
  users.users.tupi = {
    isNormalUser = true;
    extraGroups = [ "wheel" "video" "audio" "input" "tupi-ia" "tupi-st" "tupi-sec" ];
    initialPassword = "tupios";
  };

  # Usuário do autologin da ISO (vem do installation-device.nix): no grupo tupi-ia
  # ele acessa os sockets /run/tupi-perm-mgr e /run/tupi-agents (modo 0750) sem sudo.
  users.users.nixos.extraGroups = [ "tupi-ia" ];

  # Passwordless sudo for live session convenience
  security.sudo.wheelNeedsPassword = false;

  # === Boot Splash (disabled: boot verboso facilita diagnóstico) ===
  boot.plymouth.enable = false;

  # === Kernel Parameters ===
  # nouveau em blacklist: em GPUs NVIDIA o live usa o framebuffer do firmware.
  boot.kernelParams = [ "modprobe.blacklist=nouveau" ];
  boot.blacklistedKernelModules = [ "nouveau" ];

  # === TUPIOS Branding ===
  environment.etc."tupi/version".text = "0.1.0-alpha";
  environment.etc."tupi/edition".text = "installer";
}
