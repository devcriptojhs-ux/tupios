# TUPIOS — AI-Native Operating System

> Um sistema operacional orientado a IA com quatro núcleos isolados, recuperação independente e transparência total de alterações.

## Visão Geral

TUPIOS é um SO baseado em **NixOS 26.05** projetado em torno de quatro núcleos com responsabilidades bem definidas:

| Núcleo | Função |
|--------|--------|
| **PD** (Production Domain) | Ambiente de produção com GUI Wayland, onde o usuário interage normalmente |
| **ST** (Staging Environment) | Cópia isolada do PD onde a IA experimenta alterações antes do commit |
| **IA** (AI Core) | Orquestrador de agentes com modelos locais (Ollama) e remotos (API OpenAI) |
| **SEC** (Safety Core) | Recuperação independente via Alpine rescue, funcional mesmo com PD corrompido |

### Três Princípios Inegociáveis

1. **AI Independence** — O SO funciona sem IA. PD opera normalmente se a IA estiver offline.
2. **Production Integrity** — Nenhuma operação da IA compromete componentes críticos do PD diretamente.
3. **Recoverability** — Toda atualização tem rollback testável. Rollback de sistema NUNCA apaga dados do usuário.

## Status Atual

🟡 **Fase de Validação de Arquitetura** — Entregável 1 concluído, MVP em progresso.

| Entregável | Status |
|------------|--------|
| Architecture Specification (`docs/ARCHITECTURE.md`) | ✅ Concluído |
| Technical PoC (QEMU) | 🔄 Em progresso |
| TUPIOS MVP (Fases 1-5) | 🔄 Fase 1 validada (`nix flake check` ✅) |

## Estrutura do Projeto

```
tupios/
├── flake.nix                 # Top-level flake (tupi-pd, tupi-st, tupi-ia, tupi-sec)
├── justfile                  # Comandos de desenvolvimento
├── nixos/
│   ├── pd/configuration.nix  # PD: Sway + System Monitor + Permission Manager
│   ├── st/                   # ST: espelho PD + snapshots (Fase 2)
│   ├── ia/                   # IA container: Ollama + router (Fase 4)
│   ├── sec/                  # SEC: Alpine rescue (Fase 3)
│   └── modules/              # Módulos compartilhados
├── desktop/                  # Desktop próprio TUPIOS (Fase 8)
├── control-center/           # TUPIOS Control Center - Tauri + SvelteKit (Fase 6)
├── mcp-server/               # MCP server + harness engine (Fase 5)
├── agents/                   # Definições de agentes (Fase 7)
├── installer/                # Calamares module tupi-installer (Fase 9)
├── scripts/                  # Scripts de build e utilitários
├── tests/                    # Test suites (vm, security, e2e)
└── docs/                     # Documentação completa
```

## Desenvolvimento Rápido

### Pré-requisitos

- Nix com flakes habilitado
- QEMU/KVM instalado
- ~20GB de espaço livre para builds

### Comandos

```bash
# Validar flake
just check

# Bootar PD em VM (UEFI)
just vm-pd

# Bootar PD em VM (BIOS, mais simples)
just vm-pd-bios

# Build do closure PD (sem ativar)
just build-pd

# Limpar caches e artifacts
just clean
```

### Primeira vez

```bash
cd tupios
nix develop          # Entra no dev shell
just check           # Valida que tudo compila
just vm-pd-bios      # Boota PD em QEMU
```

## Documentação

| Documento | Descrição |
|-----------|-----------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Especificação técnica completa dos 4 núcleos |
| [PRD.md](../.claude/plans/vamos-construir-um-sistema-dreamy-sloth.md) | Product Requirements Document v2 |
| INSTALL.md | Guia de instalação bare metal (Fase 9) |
| SECURITY.md | Política de segurança e threat model |
| CONTRIBUTING.md | Guia para contribuidores |

## Ordem de Desenvolvimento (10 Fases)

1. ✅ **Fase 1**: NixOS + Sway + System Monitor + Btrfs subvolumes
2. 🔄 **Fase 2**: ST isolado + Btrfs snapshots + Live Preview
3. ⏳ **Fase 3**: SEC com Alpine rescue + rollback funcional
4. ⏳ **Fase 4**: IA Core com Ollama local + API externa
5. ⏳ **Fase 5**: Permission Manager + System API + Harness
6. ⏳ **Fase 6**: TUPIOS Control Center (commit manager)
7. ⏳ **Fase 7**: Agent Orchestrator + Memory Engine
8. ⏳ **Fase 8**: Desktop próprio TUPIOS (Rust + SvelteKit)
9. ⏳ **Fase 9**: ISO instalável + first-boot wizard
10. ⏳ **Fase 10**: Testes hardware real + documentação final

## Licença

A definir.

---

*TUPIOS — Sistema Operacional Orientado a IA*