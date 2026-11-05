# TUPIOS — Development Commands
# Run `just --list` to see available commands

# Default: show help
default:
    just --list

# === Build & Validate ===

# Validate flake compiles without errors
check:
    nix flake check

# Build PD system closure (dry-run, no activation)
build-pd:
    nix build .#nixosConfigurations.tupi-pd.config.system.build.toplevel

# === VM Testing ===

# Boot PD in QEMU with UEFI (OVMF)
vm-pd:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "🚀 Booting TUPIOS PD in QEMU (UEFI)..."

    RESULT=$(nix build .#nixosConfigurations.tupi-pd.config.system.build.vm --print-out-paths 2>/dev/null || \
             nix build .#nixosConfigurations.tupi-pd.config.system.build.toplevel --print-out-paths)

    qemu-system-x86_64 \
        -enable-kvm \
        -m 4G \
        -cpu host \
        -smp 4 \
        -drive file=fat:rw:/tmp/tupios-pd-disk.img,format=raw,if=virtio \
        -bios {{OVMF_PATH}} \
        -display gtk,gl=on \
        -device virtio-vga-gl \
        -netdev user,id=net0 \
        -device virtio-net-pci,netdev=net0 \
        -soundhw hda \
        -boot order=c \
        "$@"

# Boot PD in QEMU (BIOS legacy, simpler)
vm-pd-bios:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "🚀 Booting TUPIOS PD in QEMU (BIOS)..."
    nix run .#nixosConfigurations.tupi-pd.config.system.build.vm

# === Documentation ===

# Serve docs locally with live reload
docs-serve:
    cd docs && python3 -m http.server 8080

# Generate architecture diagram PNG from Mermaid
docs-diagrams:
    #!/usr/bin/env bash
    set -euo pipefail
    command -v mmdc >/dev/null 2>&1 || { echo "Install mermaid-cli: npm install -g @mermaid-js/mermaid-cli"; exit 1; }
    mkdir -p docs/diagrams
    for f in docs/ARCHITECTURE.md; do
        mmdc -i "$f" -o "docs/diagrams/$(basename $f .md).png" -b transparent
    done
    echo "✅ Diagrams generated in docs/diagrams/"

# === Cleanup ===

# Remove build artifacts and caches
clean:
    rm -rf result result-* /tmp/tupios-pd-disk.img
    nix-collect-garbage -d

# === Variables ===
OVMF_PATH := "/run/current-system/firmware/OVMF.fd"