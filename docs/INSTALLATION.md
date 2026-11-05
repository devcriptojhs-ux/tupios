# TUPIOS — Installation Guide

**Version:** 0.1.0-alpha  
**Last Updated:** 2026-11-06

---

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | x86_64 (2 cores) | x86_64 (4+ cores) |
| RAM | 4 GB | 8 GB+ (16 GB for local AI) |
| Storage | 32 GB | 64 GB+ SSD |
| GPU | Any (software rendering) | NVIDIA/AMD with Wayland support |
| Boot | UEFI (Secure Boot off) | UEFI + TPM 2.0 |
| Network | Optional (remote AI needs internet) | Ethernet or WiFi |

---

## Installation Steps

### 1. Download ISO

Download `tupios-0.1.0-alpha-x86_64.iso` from the releases page. Verify SHA-256 checksum:

```bash
sha256sum tupios-0.1.0-alpha-x86_64.iso
# Expected: <checksum-from-release-page>
```

### 2. Create Bootable USB

**Linux/macOS:**
```bash
sudo dd if=tupios-0.1.0-alpha-x86_64.iso of=/dev/sdX bs=4M status=progress conv=fsync
sync
```

**Windows:** Use Rufus, Ventoy, or balenaEtcher with GPT partition scheme for UEFI.

### 3. Boot from USB

1. Enter BIOS/UEFI setup (usually F2, F12, or DEL during POST)
2. Disable Secure Boot
3. Set USB as first boot device
4. Save and reboot

The Calamares installer will auto-start after 3 seconds. If it doesn't, open a terminal and run:
```bash
sudo calamares -D6
```

### 4. Calamares Installer

Follow the graphical installer:

1. **Welcome** — Select language (pt_BR default)
2. **Partitioning** — Choose disk. TUPIOS uses Btrfs with automatic subvolumes:
   - `@` — Root filesystem
   - `@home` — User data
   - `@var` — Variable data
   - `@nix` — Nix store
   - `@swap` — Swapfile location
   
   ⚠️ **WARNING:** All data on the selected disk will be destroyed.

3. **Users** — Create your user account (default username: `tupi`)
4. **Summary** — Review configuration
5. **Install** — Wait for installation to complete (~5-15 minutes)
6. **Finished** — Reboot into installed system

### 5. First-Boot Wizard

After reboot, the first-boot wizard runs automatically on TTY1:

```
🚀 TUPIOS First-Boot Wizard
==============================

Configure AI Backend:
 1) Remote API (OpenAI-compatible) ← DEFAULT
 2) Local Ollama (requires GPU + ~8GB RAM)

Choice [1]: 
```

**Option 1 — Remote API (Recommended):**
- Enter your OpenAI-compatible API key
- Enter base URL (default: `https://api.openai.com/v1`)
- Works immediately, no GPU required

**Option 2 — Local Ollama:**
- No API key needed
- After first login, download a model:
  ```bash
  ollama pull llama3.2        # 3B params, ~2GB
  ollama pull llama3.2:8b     # 8B params, ~5GB
  ollama pull codellama       # Code-specialized
  ```
- Enable in config: edit `/etc/tupi/ai-config.json` and set `"default_backend": "local"`

### 6. Post-Installation

Verify all cores are running:
```bash
systemctl status tupi-perm-mgr          # Permission Manager
systemctl status tupi-agent-orchestrator # Agent Orchestrator
systemctl status tupi-ai-router          # AI Router
```

Check Btrfs subvolumes:
```bash
sudo btrfs subvolume list /
# Should show @, @home, @var, @nix, @swap
```

Test AI integration:
```bash
tupi-mcp health                    # Check MCP server
tupi-memory list                   # List agent memories
tupi-perm-check cli read /etc/os-release  # Test permission check
```

---

## Upgrading

TUPIOS uses Nix flakes for atomic upgrades:

```bash
# Update flake inputs
cd /etc/nixos  # or wherever your flake lives
nix flake update

# Build and activate new generation
sudo nixos-rebuild switch --flake .#tupi-pd

# Rollback if something breaks
sudo nixos-rebuild rollback
# Or use SEC recovery TUI from GRUB menu
```

---

## Recovery

If PD becomes unbootable:

1. Reboot and select **"TUPIOS Recovery"** from GRUB/systemd-boot menu
2. The SEC rescue TUI (`tupi-recover`) launches automatically
3. Browse available Btrfs snapshots:
   ```
   [1] 2026-11-06 17:30 — Pre-AI-update snapshot
   [2] 2026-11-06 16:45 — Last known good
   [3] 2026-11-06 14:00 — Initial install
   ```
4. Select a snapshot → Confirm restore → System reboots into restored state

No internet, no external tools, no reformatting required.

---

## Troubleshooting

### Installer won't start
- Try booting with `nomodeset` kernel parameter
- Check Secure Boot is disabled in BIOS

### No display after install
- Switch to TTY2 (Ctrl+Alt+F2) and check `journalctl -u sddm`
- Try adding `modprobe.blacklist=nouveau` to kernel params for NVIDIA GPUs

### AI backend not responding
- Check `systemctl status tupi-ai-router`
- Verify `/etc/tupi/ai-config.json` has correct API key/URL
- Test connectivity: `curl -s https://api.openai.com/v1/models -H "Authorization: Bearer YOUR_KEY"`

### Permission denied errors
- Check audit log: `cat /var/log/tupi-audit/permission.log`
- Verify Permission Manager is running: `systemctl status tupi-perm-mgr`
- Test manually: `tupi-perm-check <agent_id> <operation> <target>`

### Btrfs snapshot issues
- Verify subvolumes exist: `sudo btrfs subvolume list /`
- Check disk space: `df -h /` (Btrfs shows allocated vs used differently)
- Run scrub: `sudo btrfs scrub start /`

---

## Support

- Documentation: `docs/ARCHITECTURE.md`, `docs/HARDWARE.md`
- Issues: https://github.com/tupios/tupios/issues
- Community: https://tupios.dev/community