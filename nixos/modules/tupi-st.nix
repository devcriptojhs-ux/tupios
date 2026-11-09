# TUPIOS — ST (Staging): ambiente de ensaio das mudanças de sistema, ligado sob demanda pela IA.
#
#   PD  = sistema real do usuário.
#   ST  = container NixOS "tupi-st" com a MESMA configuração do PD (flake: nixosConfigurations.tupi-st =
#         módulos do PD + nixos/st/configuration.nix). Efêmero (raiz limpa a cada vez), SEM rede e SEM o
#         /home real. Não liga no boot: o executor da IA (tupi-agent-exec) constrói o sistema do ensaio,
#         aponta o perfil do container para ele e liga o container só para testar.
#   SEC = resgate independente (nixos/sec).
#
# Limite: o container usa o MESMO kernel do PD — sysctl/parâmetros de boot não são ensaiados nele.
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.st;
  profileDir = "/nix/var/nix/profiles/per-container/${cfg.containerName}";

  # Pontos de retorno do PD. Nada de "mv /": snapshots são subvolumes Btrfs no nível superior do
  # volume e a volta segura e imediata é pela GERAÇÃO NixOS; restaurar um snapshot troca o subvolume
  # da raiz para o próximo boot (o /home é outro subvolume e não é tocado).
  tupiSnapshot = pkgs.writeShellApplication {
    name = "tupi-snapshot";
    runtimeInputs = with pkgs; [ btrfs-progs util-linux coreutils gnugrep gnused nix ];
    # SC2012: listar nomes de snapshot com ls é intencional; SC2015: "a && b || erro" é intencional
    excludeShellChecks = [ "SC2012" "SC2015" ];
    text = ''
      PROFILE=/nix/var/nix/profiles/system
      CMD="''${1:-help}"

      need_root() { [ "$(id -u)" = 0 ] || { echo "precisa ser root (sudo)"; exit 1; }; }
      root_fs() { findmnt -n -o FSTYPE /; }
      # dispositivo e subvolume da raiz: "/dev/mapper/tupi-root[/@pd-root]"
      root_dev() { findmnt -n -o SOURCE / | sed 's/\[.*\]$//'; }
      root_subvol() { findmnt -n -o FSROOT / | sed 's#^/##'; }
      TOP=/run/tupi-btrfs-top
      mount_top() {
        mkdir -p "$TOP"
        mountpoint -q "$TOP" || mount -o subvolid=5 "$(root_dev)" "$TOP"
        mkdir -p "$TOP/@pd-snapshots"
      }
      umount_top() { umount "$TOP" 2>/dev/null || true; }

      case "$CMD" in
        create)
          need_root
          [ "$(root_fs)" = btrfs ] || { echo "a raiz não é Btrfs: use as gerações NixOS (tupi-snapshot generations)"; exit 2; }
          NAME="''${2:-pd-$(date +%Y%m%d-%H%M%S)}"
          case "$NAME" in *[!A-Za-z0-9._-]*) echo "nome inválido"; exit 2;; esac
          mount_top
          trap umount_top EXIT
          btrfs subvolume snapshot -r / "$TOP/@pd-snapshots/$NAME" >/dev/null
          echo "$NAME"
          ;;
        list)
          if [ "$(root_fs)" = btrfs ] && [ "$(id -u)" = 0 ]; then
            mount_top; trap umount_top EXIT
            echo "Snapshots Btrfs da raiz:"; ls -1 "$TOP/@pd-snapshots" 2>/dev/null | sort -r | sed 's/^/  /'
          fi
          echo "Gerações NixOS:"; nix-env -p "$PROFILE" --list-generations | sed 's/^/  /'
          ;;
        generations)
          nix-env -p "$PROFILE" --list-generations
          ;;
        rollback)
          # volta (agora, sem reiniciar) para a geração anterior ou para a geração N
          need_root
          if [ -n "''${2:-}" ]; then nix-env -p "$PROFILE" --switch-generation "$2"; else nix-env -p "$PROFILE" --rollback; fi
          "$PROFILE/bin/switch-to-configuration" switch
          ;;
        restore)
          # troca o subvolume da raiz pelo snapshot NO PRÓXIMO BOOT; o atual fica guardado ao lado
          need_root
          NAME="''${2:-}"
          [ -n "$NAME" ] || { echo "uso: tupi-snapshot restore <snapshot> --confirmar"; exit 1; }
          [ "''${3:-}" = "--confirmar" ] || { echo "Isto troca a raiz do sistema no próximo boot (o /home não muda). Repita com --confirmar."; exit 1; }
          [ "$(root_fs)" = btrfs ] || { echo "a raiz não é Btrfs"; exit 2; }
          SUB="$(root_subvol)"
          mount_top
          trap umount_top EXIT
          [ -d "$TOP/@pd-snapshots/$NAME" ] || { echo "snapshot não encontrado: $NAME"; exit 1; }
          [ -n "$SUB" ] && [ -d "$TOP/$SUB" ] || { echo "subvolume da raiz não encontrado ($SUB)"; exit 1; }
          OLD="$SUB.antes-restore-$(date +%Y%m%d-%H%M%S)"
          btrfs subvolume snapshot "$TOP/@pd-snapshots/$NAME" "$TOP/$SUB.restaurando" >/dev/null
          mv "$TOP/$SUB" "$TOP/$OLD"          # renomeia o subvolume (a raiz montada continua funcionando)
          mv "$TOP/$SUB.restaurando" "$TOP/$SUB"
          echo "Pronto: no próximo boot a raiz será o snapshot $NAME."
          echo "A raiz atual foi guardada como $OLD (apague depois com: btrfs subvolume delete)."
          ;;
        delete)
          need_root
          NAME="''${2:-}"
          case "$NAME" in ""|*[!A-Za-z0-9._-]*) echo "nome inválido"; exit 2;; esac
          mount_top
          trap umount_top EXIT
          btrfs subvolume delete "$TOP/@pd-snapshots/$NAME"
          ;;
        *)
          echo "TUPIOS — pontos de retorno do PD"
          echo "  tupi-snapshot create [nome]            snapshot Btrfs da raiz (somente leitura)"
          echo "  tupi-snapshot list | generations       snapshots e gerações NixOS"
          echo "  tupi-snapshot rollback [geração]       volta AGORA para a geração anterior (ou a N)"
          echo "  tupi-snapshot restore <nome> --confirmar  raiz = snapshot no próximo boot (/home intacto)"
          echo "  tupi-snapshot delete <nome>"
          ;;
      esac
    '';
  };
in
{
  options.tupi.st = {
    enable = lib.mkEnableOption "ST: container de ensaio das mudanças de sistema (usado pela IA antes do PD)";

    containerName = lib.mkOption {
      type = lib.types.str;
      default = "tupi-st";
      readOnly = true;
      description = "Nome do container do ST.";
    };

    snapshotTool = lib.mkOption {
      type = lib.types.package;
      default = tupiSnapshot;
      readOnly = true;
      description = "tupi-snapshot (pontos de retorno do PD).";
    };
  };

  config = lib.mkMerge [
    { environment.systemPackages = [ tupiSnapshot ]; }

    (lib.mkIf cfg.enable {
      boot.enableContainers = true;

      # O sistema do container NÃO é fixo aqui: é o perfil que o executor aponta para a configuração
      # (atual ou em ensaio) construída a partir de /etc/nixos — assim o ST é sempre "o PD + a mudança".
      # O caminho é lido no HOST (checagem de arquitetura) e DENTRO do container (o init), onde o nspawn
      # monta ${profileDir} em /nix/var/nix/profiles. Para valer nos dois lados, o tmpfiles abaixo cria
      # dentro do perfil o link relativo per-container/<nome>/system -> ../../system.
      containers.${cfg.containerName} = {
        path = "${profileDir}/system";
        autoStart = false;
        ephemeral = true;        # raiz limpa a cada vez que liga
        privateNetwork = true;   # sem hostAddress/localAddress: só loopback, sem rede
      };

      systemd.tmpfiles.rules = [
        "d ${profileDir} 0755 root root -"
        "d ${profileDir}/per-container 0755 root root -"
        "d ${profileDir}/per-container/${cfg.containerName} 0755 root root -"
        # dentro do container: /nix/var/nix/profiles/per-container/<nome>/system -> /nix/var/nix/profiles/system
        "L+ ${profileDir}/per-container/${cfg.containerName}/system - - - - ../../system"
      ];
    })
  ];
}
