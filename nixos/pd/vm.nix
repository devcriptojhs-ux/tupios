# TUPIOS PD — ajustes que valem SÓ na VM de teste (nixosConfigurations.tupi-pd.config.system.build.vm).
# Não mudam o sistema instalado: tudo fica dentro de virtualisation.vmVariant.
{ lib, ... }:

{
  virtualisation.vmVariant = {
    virtualisation = {
      memorySize = 5120;
      cores = 4;
      diskSize = 24576;                  # MB: o ensaio no ST constrói sistemas dentro da VM
      writableStoreUseTmpfs = false;     # o que for construído/baixado vai para o disco, não para a RAM
      graphics = true;
    };
    # A VM sobe direto pelo kernel (sem partição de boot): sem isto, trocar de geração (st_promote,
    # Desfazer) tentaria instalar o systemd-boot e falharia ("Failed to install bootloader").
    boot.loader.systemd-boot.enable = lib.mkForce false;
    boot.loader.grub.enable = lib.mkForce false;
    # Teste automatizado pela serial: root entra direto no console serial e o usuário na interface
    services.getty.autologinUser = lib.mkForce "root";
    tupi.classicDesktop.autoLoginUser = "tupi";
    users.users.tupi.initialPassword = "tupios";
    # Na VM, a IA ensaia e promove a própria variante de VM (flake: nixosConfigurations.tupi-pd-vm)
    tupi.agent.flakeRef = "/etc/nixos#tupi-pd-vm";
  };
}
