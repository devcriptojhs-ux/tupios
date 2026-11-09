# TUPIOS — ST (Staging): a configuração do PD rodando como container de ensaio.
#
# O flake monta o ST assim:  nixosConfigurations.tupi-st = módulos do PD + ESTE arquivo.
# Ou seja: o ST é o PD (mesmos pacotes, serviços e opções, inclusive o que a IA estiver ensaiando),
# só que adaptado para container:
#   - sem bootloader, kernel, initrd, LUKS, discos e swap (o container usa o kernel do PD);
#   - sem interface gráfica, som e hardware (não existem dentro do container);
#   - sem a própria IA (Permission Manager, executor, router, app) — ela é quem opera o ST de fora;
#   - sem rede (privateNetwork no host) e com /home vazio (efêmero), nunca o /home real.
# O container é ligado SOB DEMANDA pelo executor (containers.tupi-st em nixos/modules/tupi-st.nix).
#
# Limite: parâmetros do kernel (sysctl globais, boot.kernelParams) não são ensaiados aqui: o kernel é o
# mesmo do PD. A IA avisa isso e oferece set_kernel_param (temporário, com Desfazer).
{ lib, ... }:

{
  boot.isContainer = true;
  networking.hostName = lib.mkForce "tupios-st";

  # Hardware/boot do PD não existem no container
  boot.loader.systemd-boot.enable = lib.mkForce false;
  boot.loader.efi.canTouchEfiVariables = lib.mkForce false;
  boot.initrd.luks.devices = lib.mkForce { };
  fileSystems = lib.mkForce { };
  swapDevices = lib.mkForce [ ];
  networking.networkmanager.enable = lib.mkForce false;
  # sem rede no ST: o DHCP só atrasaria o boot do container (~90 s esperando)
  networking.useDHCP = lib.mkForce false;
  networking.dhcpcd.enable = lib.mkForce false;
  security.apparmor.enable = lib.mkForce false;   # o container não carrega perfis no kernel do host

  # Sem interface gráfica nem som dentro do container
  tupi.classicDesktop.enable = lib.mkForce false;
  tupi.desktop.enable = lib.mkForce false;
  programs.sway.enable = lib.mkForce false;
  xdg.portal.enable = lib.mkForce false;
  xdg.portal.wlr.enable = lib.mkForce false;
  services.xserver.enable = lib.mkForce false;
  services.pipewire.enable = lib.mkForce false;

  # A IA não roda dentro do próprio ensaio (e o ST não tem um ST dentro dele)
  tupi.agent.enable = lib.mkForce false;
  tupi.st.enable = lib.mkForce false;
  tupi.permissionManager.enable = lib.mkForce false;
  tupi.agentOrchestrator.enable = lib.mkForce false;
  tupi.aiRouter.enable = lib.mkForce false;
  tupi.assistant.enable = lib.mkForce false;

  environment.etc."tupi/core".text = lib.mkForce "st";
}
