# TUPIOS — Hardware Compatibility Matrix

**Version:** 0.1.0-alpha  
**Last Updated:** 2026-11-06  
**Tested Configurations:** Reference hardware list below

---

## Supported Architectures

| Architecture | Status | Notes |
|-------------|--------|-------|
| x86_64 (AMD64) | ✅ Primary | Full support, ISO available |
| aarch64 (ARM64) | ⏳ Planned | NixOS supports it; TUPIOS modules untested |
| i686 (32-bit) | ❌ Not supported | NixOS dropped 32-bit; Wayland requires 64-bit |

---

## CPU Compatibility

| Vendor | Generation | Status | Notes |
|--------|-----------|--------|-------|
| Intel | 6th Gen+ (Skylake) | ✅ Tested | Core i5/i7/i9, Xeon E3/E5 |
| Intel | 4th-5th Gen | ⚠️ Untested | Should work; report issues |
| AMD | Ryzen 1000+ | ✅ Tested | Ryzen 3/5/7/9, Threadripper |
| AMD | FX/A-Series | ⚠️ Untested | May need kernel params |
| Apple | M1/M2/M3 | ❌ Not supported | ARM64; see aarch64 roadmap |

**Minimum:** Any x86_64 CPU with SSE4.2 and POPCNT instructions.

---

## GPU / Display

### Integrated Graphics

| GPU | Wayland | Sway | Native Desktop | Notes |
|-----|---------|------|----------------|-------|
| Intel UHD 620+ | ✅ | ✅ | ✅ | Best Wayland support |
| Intel Iris Xe | ✅ | ✅ | ✅ | Recommended for laptops |
| AMD Radeon Vega | ✅ | ✅ | ✅ | Mesa 23.0+ required |
| AMD RDNA2/3 | ✅ | ✅ | ✅ | Excellent Wayland support |
| NVIDIA (Optimus) | ⚠️ | ⚠️ | ⚠️ | Requires `nvidia-drm.modeset=1` |

### Discrete GPUs

| GPU | Wayland | Sway | Native Desktop | Notes |
|-----|---------|------|----------------|-------|
| NVIDIA GTX 1050+ | ⚠️ | ⚠️ | ⚠️ | Proprietary driver; add to kernel params |
| NVIDIA RTX 20/30/40 | ⚠️ | ⚠️ | ⚠️ | Same as above; AI local needs CUDA |
| AMD RX 5000+ | ✅ | ✅ | ✅ | Open-source amdgpu driver |
| AMD RX 6000/7000 | ✅ | ✅ | ✅ | Recommended for AI local |

### NVIDIA Setup

Add to `/etc/nixos/configuration.nix`:
```nix
boot.kernelParams = [ "nvidia-drm.modeset=1" ];
services.xserver.videoDrivers = [ "nvidia" ];
hardware.opengl.enable = true;
```

For hybrid laptops (Intel + NVIDIA), use `envycontrol` or `supergfxctl` to switch GPUs.

---

## Storage

| Type | Btrfs | Installation | Notes |
|------|-------|-------------|-------|
| NVMe SSD | ✅ | ✅ | Recommended; fastest snapshots |
| SATA SSD | ✅ | ✅ | Fully supported |
| SATA HDD | ✅ | ✅ | Works but slow for AI/memory DB |
| USB 3.0 External | ✅ | ✅ | For live ISO testing only |
| eMMC | ⚠️ | ⚠️ | Limited write endurance; not recommended |
| RAID (mdadm) | ⚠️ | ⚠️ | Manual setup required post-install |

**Minimum:** 32 GB free space. **Recommended:** 64 GB+ NVMe/SATA SSD.

Btrfs subvolumes (`@`, `@home`, `@var`, `@nix`, `@swap`) are created automatically during installation.

---

## Networking

### Ethernet

| Controller | Status | Notes |
|-----------|--------|-------|
| Intel I219/I225/I226 | ✅ | Most common; fully supported |
| Realtek RTL8111/8168 | ✅ | Widely compatible |
| Broadcom BCM57xx | ✅ | Server-grade; supported |
| Qualcomm Atheros | ✅ | Laptop integrated; supported |

### WiFi

| Chipset | Status | Notes |
|---------|--------|-------|
| Intel AX200/AX201/AX210 | ✅ | Best Linux WiFi support |
| Intel AC 8260/8265/9260 | ✅ | Older but stable |
| MediaTek MT7921/MT7922 | ✅ | Common in AMD laptops |
| Realtek RTL8822CE | ⚠️ | May need firmware package |
| Broadcom BCM43xx | ⚠️ | Proprietary firmware; install `broadcom-wl` |
| Qualcomm QCA6174 | ⚠️ | Some models need manual firmware |

**Live ISO includes:** NetworkManager GUI + `nmcli` CLI for WiFi configuration during install.

---

## Audio

| Backend | Status | Notes |
|---------|--------|-------|
| PipeWire | ✅ Default | Modern; replaces PulseAudio |
| PulseAudio | ✅ Fallback | Legacy compatibility |
| ALSA direct | ✅ | Low-latency; manual config |
| JACK | ⚠️ | Via PipeWire JACK bridge |

Most HDA (Intel/Realtek) and USB audio devices work out of the box.

---

## Input Devices

| Device | Status | Notes |
|--------|--------|-------|
| USB Keyboard/Mouse | ✅ | Plug and play |
| PS/2 Keyboard/Mouse | ✅ | Legacy support |
| Touchpad (Synaptics/ELAN) | ✅ | libinput; gestures via swaygestures |
| Touchscreen | ⚠️ | Basic touch works; gestures limited |
| Wacom Tablet | ⚠️ | Opentabletdriver; may need config |
| Gamepad (Xbox/PS) | ✅ | evdev; works in games/emulators |
| Fingerprint Reader | ❌ | No fprintd integration yet |

---

## Boot Firmware

| Firmware | Status | Notes |
|----------|--------|-------|
| UEFI 2.4+ | ✅ Primary | Required for Secure Boot (disabled) |
| Legacy BIOS | ✅ Supported | GRUB legacy mode; no Secure Boot |
| Secure Boot | ❌ Disabled | Must be turned off in BIOS setup |
| TPM 2.0 | ⚠️ Optional | Not used currently; future LUKS unlock |

---

## Peripherals

| Peripheral | Status | Notes |
|-----------|--------|-------|
| USB-C Dock | ✅ | DisplayPort Alt Mode + PD charging |
| Thunderbolt 3/4 | ⚠️ | Basic support; no daisy-chain tested |
| Bluetooth 5.0+ | ✅ | BlueZ; audio + HID devices |
| Webcam (UVC) | ✅ | Most USB webcams; v4l2loopback for virtual |
| Printer (CUPS) | ⚠️ | Install `system-config-printer` post-install |
| Scanner (SANE) | ⚠️ | Depends on model; check SANE backend list |

---

## Known Issues & Workarounds

### NVIDIA Black Screen After Install
**Symptom:** System boots but display stays black after GRUB.  
**Fix:** Add `nomodeset` to kernel params at GRUB menu, then permanently add `nvidia-drm.modeset=1` to NixOS config.

### WiFi Not Detected in Live ISO
**Symptom:** No wireless networks shown in NetworkManager.  
**Fix:** Check `lspci | grep -i network` for chipset. If Broadcom, run `sudo modprobe wl`. If Realtek, try `sudo modprobe rtw88_8822ce`.

### Btrfs Subvolume Creation Fails
**Symptom:** First-boot wizard reports "Btrfs not detected on root."  
**Fix:** Ensure disk was formatted as Btrfs during Calamares partitioning. Re-run installer with manual partitioning if needed.

### AI Router Connection Timeout
**Symptom:** `tupi-mcp health` returns timeout.  
**Fix:** Verify `/etc/tupi/ai-config.json` has correct API key and base URL. Test with `curl -s https://api.openai.com/v1/models -H "Authorization: Bearer YOUR_KEY"`. Check firewall isn't blocking outbound HTTPS.

### Permission Manager Socket Missing
**Symptom:** `tupi-perm-check` returns "Permission Manager not running."  
**Fix:** Run `sudo systemctl enable --now tupi-perm-mgr.service`. Check logs: `journalctl -u tupi-perm-mgr -f`.

---

## Reporting Hardware Issues

If your hardware isn't listed or has problems:

1. Collect system info:
   ```bash
   sudo lspci > /tmp/lspci.txt
   sudo lsusb > /tmp/lsusb.txt
   dmesg > /tmp/dmesg.txt
   nix-info -m > /tmp/nix-info.txt
   ```

2. Open an issue at https://github.com/tupios/tupios/issues with:
   - Hardware model/specs
   - Which files from step 1 are relevant
   - What worked and what didn't
   - Any workarounds you found

Community contributions to this matrix are welcome!

---

## Reference Test Hardware

| Machine | CPU | GPU | RAM | Storage | Status |
|---------|-----|-----|-----|---------|--------|
| Dell XPS 15 9520 | i7-12700H | RTX 3050 Ti | 32 GB | 1TB NVMe | ✅ Full |
| Lenovo ThinkPad T14s G3 | Ryzen 7 PRO 6850U | Radeon 680M | 16 GB | 512GB NVMe | ✅ Full |
| Custom Desktop | Ryzen 9 5900X | RX 6800 XT | 64 GB | 2TB NVMe | ✅ Full |
| Intel NUC 11 | i5-1135G7 | Iris Xe | 16 GB | 500GB SATA | ✅ Full |
| HP EliteDesk 800 G5 | i5-9500 | UHD 630 | 8 GB | 256GB NVMe | ✅ Basic |
| MacBook Pro 2019 | i9-9980HK | Radeon Pro 5500M | 32 GB | 1TB NVMe | ⚠️ Partial* |

*\*MacBook: Requires Asahi Linux kernel patches for Apple Silicon; Intel Macs work but need `apple-bce` module for keyboard/trackpad.*

---

## Future Hardware Roadmap

- **ARM64 (Apple Silicon):** Tracking Asahi Linux progress; ETA TBD
- **RISC-V:** Monitoring NixOS RISC-V port; experimental only
- **NVIDIA Wayland Native:** Waiting for NVIDIA open-source kernel modules to mature
- **Thunderbolt 4 Daisy-Chain:** Testing planned for Q1 2027
- **Fingerprint Authentication:** Integrating fprintd + PAM module
- **TPM 2.0 LUKS Unlock:** Automated encrypted boot with TPM key escrow