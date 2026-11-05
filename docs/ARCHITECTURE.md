# TUPIOS — Architecture Documentation

**Version:** 0.1.0-alpha  
**Last Updated:** 2026-11-06  
**Status:** Release Candidate

---

## Overview

TUPIOS is an AI-Native Operating System built on NixOS that separates system concerns into four isolated cores: **PD** (Production Domain), **ST** (Staging Environment), **IA** (AI Core), and **SEC** (Safety Core). This architecture ensures the production environment never breaks due to AI-generated changes, while providing safe rollback, permission-controlled AI operations, and persistent agent memory.

---

## Core Architecture

### PD — Production Domain

The user-facing environment where all interaction occurs. PD runs the native TUPIOS Wayland desktop (with Sway fallback), terminal emulators, file managers, and application launchers. It is **immutable by direct AI modification** — all changes must pass through ST first.

**Key Properties:**
- Btrfs root filesystem with read-only `/nix/store`
- Wayland compositor (native Rust + Smithay, Sway fallback during transition)
- Permission Manager validates all AI-originated operations before execution
- User data in `/home` is writable; system config is declarative via Nix

**Module:** `nixos/pd/configuration.nix`

### ST — Staging Environment

A dormant copy of PD where AI agents make and test changes before promotion. ST uses Btrfs subvolumes for instant snapshot/rollback and is completely isolated from PD at the filesystem level.

**Key Properties:**
- Btrfs subvolume `/var/lib/tupi-st` mirrors PD structure
- CLI `tupi-snapshot` for create/list/restore/delete snapshots
- Changes promoted to PD via atomic subvolume swap after Harness validation
- No user sessions run in ST — only AI agent operations

**Module:** `nixos/st/configuration.nix`

### IA — AI Core

Hosts the AI Router, Agent Orchestrator, and Memory Engine. Supports both remote API backends (OpenAI-compatible, default) and optional local Ollama inference. All AI operations are routed through the Permission Manager before execution.

**Key Properties:**
- AI Router Python service with backend abstraction layer
- Agent Orchestrator with MCP (Model Context Protocol) Unix socket server
- SQLite Memory Engine for persistent agent context across sessions
- Remote backend is default; local Ollama enabled via `tupi.ai.local.enable = true`

**Modules:** `nixos/ia/configuration.nix`, `nixos/modules/agent-orchestrator.nix`

### SEC — Safety Core

Independent rescue environment based on Alpine Linux that can restore any previous PD state without requiring the main system to boot. SEC has its own kernel, initramfs, and recovery TUI.

**Key Properties:**
- Minimal Alpine-based rescue system (~200MB)
- TUI `tupi-recover` for browsing and restoring Btrfs snapshots
- Atomic rollback via `btrfs subvolume set-default`
- Accessible via GRUB/systemd-boot menu even if PD is completely broken

**Module:** `nixos/sec/configuration.nix`

---

## Cross-Cutting Modules

### Permission Manager (Phase 5)

Validates every AI operation against a declarative JSON policy before execution. Runs as a systemd service exposing a Unix socket API.

**Policy Categories:**
| Category | Default Action | Description |
|----------|---------------|-------------|
| `read` | allow | File reads, directory listings |
| `write_user_data` | ask | Writes to `/home`, `/var/lib/tupi-st` |
| `write_system_config` | ask | Nix config changes, `/etc` modifications |
| `install_package` | ask | `nix-env`, package installations |
| `network_egress` | ask | Outbound network connections |
| `execute_binary` | ask | Spawning new processes |
| `modify_bootloader` | deny | Boot configuration changes |
| `access_hardware` | deny | Direct hardware/sysfs access |
| `privilege_escalation` | deny | sudo, setuid, capability changes |
| `delete_system_file` | deny | Removing files from `/nix/store`, `/etc` |

**Audit Log:** Append-only with SHA-256 hash chain for tamper detection at `/var/log/tupi-audit/permission.log`.

**CLI:** `tupi-perm-check <agent_id> <operation> [target]`

### Harness Engine (Phase 5)

Pre-commit validation gate that checks all pending operations in a manifest against the Permission Manager before allowing ST→PD promotion.

**CLI:** `tupi-harness-validate [manifest-path]`

### Agent Orchestrator (Phase 7)

Coordinates multiple AI agents with persistent memory and standardized MCP communication.

**Memory Engine Tables:**
- `agent_memory` — Persistent context per agent/session with TTL expiration
- `agent_sessions` — Session lifecycle tracking
- `mcp_messages` — Inter-agent message queue with acknowledgment

**MCP Methods:** `memory/store`, `memory/recall`, `session/create`, `message/send`, `agents/list`, `health`

**CLIs:** `tupi-mcp <method> [agent_id] [params]`, `tupi-memory {list|sessions|messages|cleanup}`

### TUPIOS Desktop (Phase 8)

Native Wayland compositor built with Rust + Smithay, currently using Sway as a transitional fallback with full TUPIOS theming.

**Theme:** Primary color `#52EA23`, JetBrains Mono font, dark background `#000000`

**Packages:** alacritty, wofi, waybar, mako, grim, slurp, wl-clipboard, brightnessctl, pamixer, playerctl

### Control Center (Phase 6)

Tauri + SvelteKit GUI application for managing commits, approving permissions, and monitoring system status. Integrates with Permission Manager via Unix socket and displays real-time core status.

**Location:** `apps/control-center/`

---

## Data Flow

```
User Request → AI Agent → Permission Manager → [allow/deny/ask]
                                    ↓ (allow)
                              ST Environment
                                    ↓ (Harness validated)
                              PD Promotion (atomic Btrfs swap)
                                    ↓
                              User sees change in PD
                                    
Failure at any point → SEC Recovery TUI → Snapshot Restore → PD restored
```

---

## Build & Validation

All configurations are validated with `nix flake check --all-systems`:

```bash
nix flake check          # Validate all 5 configs (PD, ST, SEC, IA, ISO)
nix build .#tupi-pd      # Build PD system closure
nix build .#tupi-iso     # Build installable ISO image
```

---

## Security Model

1. **Declarative immutability** — System state defined in Nix, no ad-hoc mutations
2. **Permission gating** — Every AI operation validated before execution
3. **Append-only audit** — Tamper-evident log with hash chaining
4. **Isolated cores** — ST/SEC cannot corrupt PD without explicit promotion
5. **Atomic rollbacks** — Btrfs subvolume swap is instantaneous and reversible
6. **No privilege escalation** — AI agents cannot gain root or modify bootloader

---

## File Structure

```
tupios/
├── flake.nix                    # Flake entry point with all 5 configs
├── nixos/
│   ├── pd/configuration.nix     # Production Domain
│   ├── st/configuration.nix     # Staging Environment
│   ├── sec/configuration.nix    # Safety Core
│   ├── ia/configuration.nix     # AI Core
│   ├── iso/configuration.nix    # Installer ISO
│   └── modules/
│       ├── permission-manager.nix
│       ├── agent-orchestrator.nix
│       ├── tupi-desktop.nix
│       └── tupi-installer.nix
├── apps/
│   └── control-center/          # Tauri + SvelteKit GUI
└── docs/
    ├── ARCHITECTURE.md          # This document
    ├── INSTALLATION.md          # Installation guide
    ├── HARDWARE.md              # Hardware compatibility
    └── dashboard.html           # Progress dashboard
```