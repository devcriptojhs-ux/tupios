#!/usr/bin/env bash
# TUPIOS — Partition & Btrfs Setup Script
# Uso: sudo ./tupi-partition.sh /dev/sdX
# Cria layout Btrfs + LUKS + EFI conforme ARCHITECTURE.md §5

set -euo pipefail

DISK="${1:-}"
if [ -z "$DISK" ]; then
    echo "❌ Uso: sudo $0 /dev/sdX"
    echo "   Exemplo: sudo $0 /dev/nvme0n1"
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "❌ Este script deve ser executado como root"
    exit 1
fi

if [ ! -b "$DISK" ]; then
    echo "❌ Dispositivo não encontrado: $DISK"
    exit 1
fi

echo "⚠️  ATENÇÃO: Todos os dados em $DISK serão apagados!"
read -rp "Digite YES para confirmar: " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
    echo "❌ Cancelado."
    exit 1
fi

echo ""
echo "🔧 Particionando $DISK..."

# === 1. Limpar disco ===
wipefs -a "$DISK"
sgdisk --zap-all "$DISK"

# === 2. Criar partições ===
# Partição 1: EFI System Partition (512MB)
sgdisk -n 1:0:+512M -t 1:EF00 -c 1:"EFI" "$DISK"

# Partição 2: SEC Rescue Partition (200MB, FAT32)
sgdisk -n 2:0:+200M -t 2:EF00 -c 2:"TUPI-RESCUE" "$DISK"

# Partição 3: LUKS Root (resto do disco)
sgdisk -n 3:0:0 -t 3:8309 -c 3:"TUPI-ROOT" "$DISK"

partprobe "$DISK"
sleep 2

# Detectar nomes das partições
if [[ "$DISK" == *"nvme"* ]]; then
    EFI_PART="${DISK}p1"
    RESCUE_PART="${DISK}p2"
    ROOT_PART="${DISK}p3"
else
    EFI_PART="${DISK}1"
    RESCUE_PART="${DISK}2"
    ROOT_PART="${DISK}3"
fi

echo ""
echo "🔐 Configurando LUKS em $ROOT_PART..."
cryptsetup luksFormat \
    --type luks2 \
    --cipher aes-xts-plain64 \
    --key-size 512 \
    --hash sha512 \
    --pbkdf argon2id \
    --iter-time 4000 \
    --label tupi-root \
    "$ROOT_PART"

echo ""
echo "🔓 Abrindo LUKS..."
cryptsetup open "$ROOT_PART" tupi-root

LUKS_DEV="/dev/mapper/tupi-root"

echo ""
echo "📁 Formatando filesystems..."

# EFI partition
mkfs.vfat -F 32 -n EFI "$EFI_PART"

# Rescue partition
mkfs.vfat -F 32 -n TUPI-RESCUE "$RESCUE_PART"

# Root Btrfs
mkfs.btrfs -L tupi-root "$LUKS_DEV"

echo ""
echo "🗂️  Criando subvolumes Btrfs..."
mount "$LUKS_DEV" /mnt

btrfs subvolume create /mnt/@pd-root
btrfs subvolume create /mnt/@home
btrfs subvolume create /mnt/@nix-store
btrfs subvolume create /mnt/@st-workspace
btrfs subvolume create /mnt/@st-snapshots
btrfs subvolume create /mnt/@var-lib-tupi
btrfs subvolume create /mnt/@swap

umount /mnt

echo ""
echo "📂 Montando subvolumes para instalação..."

# Montar root subvolume
mount -o subvol=@pd-root,compress=zstd,noatime "$LUKS_DEV" /mnt

# Criar pontos de montagem
mkdir -p /mnt/{home,nix/store,var/lib/tupi-st/snapshots,var/lib/tupi-ia,var/lib/tupi-sec,var/log/tupi-audit,boot,efi/tupi-rescue}

# Montar subvolumes
mount -o subvol=@home,compress=zstd,noatime "$LUKS_DEV" /mnt/home
mount -o subvol=@nix-store,compress=zstd,noatime "$LUKS_DEV" /mnt/nix/store
mount -o subvol=@st-workspace,compress=zstd,noatime "$LUKS_DEV" /mnt/var/lib/tupi-st
mount -o subvol=@st-snapshots,compress=zstd,noatime "$LUKS_DEV" /mnt/var/lib/tupi-st/snapshots
mount -o subvol=@var-lib-tupi,compress=zstd,noatime "$LUKS_DEV" /mnt/var/lib/tupi-ia
mount -o bind /mnt/var/lib/tupi-ia /mnt/var/lib/tupi-sec  # Será separado depois

# Montar EFI
mount "$EFI_PART" /mnt/boot
mount "$RESCUE_PART" /mnt/efi/tupi-rescue

echo ""
echo "✅ Particionamento concluído!"
echo ""
echo "Layout criado:"
echo "  $EFI_PART     → /boot (EFI, 512MB)"
echo "  $RESCUE_PART  → /efi/tupi-rescue (SEC, 200MB)"
echo "  $ROOT_PART    → LUKS → Btrfs com subvolumes:"
echo "    @pd-root       → /"
echo "    @home          → /home"
echo "    @nix-store     → /nix/store"
echo "    @st-workspace  → /var/lib/tupi-st"
echo "    @st-snapshots  → /var/lib/tupi-st/snapshots"
echo "    @var-lib-tupi  → /var/lib/tupi-{ia,sec,audit}"
echo "    @swap          → (swapfile se necessário)"
echo ""
echo "📋 Próximos passos:"
echo "  1. nixos-install --flake .#tupi-pd --root /mnt"
echo "  2. Copiar rescue rootfs para /mnt/efi/tupi-rescue/"
echo "  3. Configurar systemd-boot entries"
echo "  4. Reboot"