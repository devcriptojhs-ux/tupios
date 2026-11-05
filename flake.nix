{
  description = "TUPIOS — AI-Native Operating System";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    let
      supportedSystems = [ "x86_64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      # NixOS configurations for each core
      nixosConfigurations = {
        # PD — Production Domain (Fase 1)
        tupi-pd = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            ./nixos/pd/configuration.nix
          ];
        };

        # ST — Staging Environment (Fase 2)
        tupi-st = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            ./nixos/st/configuration.nix
          ];
        };

        # IA — AI Core container (Fase 4)
        # Backend remoto é o padrão; Ollama local habilitável via tupi.ai.local.enable = true
        tupi-ia = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            ./nixos/ia/configuration.nix
          ];
        };

        # SEC — Safety Core rescue (Fase 3)
        tupi-sec = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            ./nixos/sec/configuration.nix
          ];
        };

        # ISO — Bootable Installer Image (Fase 9)
        tupi-iso = nixpkgs.lib.nixosSystem {
          system = "x86_64-linux";
          modules = [
            "${nixpkgs}/nixos/modules/installer/cd-dvd/installation-cd-minimal.nix"
            ./nixos/iso/configuration.nix
          ];
        };
      };

      # Development shells
      devShells = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in
        {
          default = pkgs.mkShell {
            packages = with pkgs; [
              nixos-rebuild
              btrfs-progs
              qemu_kvm
              OVMF
              git
              just
            ];
            shellHook = ''
              echo "🟢 TUPIOS development environment"
              echo "   Run 'just vm-pd' to boot PD in QEMU"
              echo "   Run 'nix flake check' to validate flake"
            '';
          };
        }
      );

      # Packages
      packages = forAllSystems (system:
        let pkgs = nixpkgs.legacyPackages.${system}; in
        {
          # Scripts de utilidade
          tupi-boot-splash = pkgs.writeShellScriptBin "tupi-boot-splash" ''
            #!/bin/sh
            if [ -f /etc/tupi/boot-logo.txt ]; then
              cat /etc/tupi/boot-logo.txt
            else
              echo "TUPIOS"
            fi
          '';
        }
      );
    };
}