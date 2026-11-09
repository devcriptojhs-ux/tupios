"""Política de permissões do agente TUPIOS.

Fonte única de verdade para: categorias, modos (restrito / perguntar / livre), combinação
das camadas (modo -> overrides do NixOS -> /etc/tupi/agent-policy.json), classificação das
chamadas de ferramenta (inclusive comandos de shell) e a decisão allow / ask / deny.

Invariantes que NENHUMA configuração muda:
  - a IA nunca altera a própria política nem o log de auditoria (self_policy -> deny);
  - comandos destrutivos sempre pedem confirmação extra, mesmo no modo livre;
  - segredos (read_secret) só são liberados por ajuste explícito dessa categoria.
"""
import os
import re
import shlex
import time

# ------------------------------------------------------------------ categorias
CATEGORIES = [
    "read",
    "read_secret",
    "write_user_data",
    "write_system_config",
    "install_package",
    "stage_change",
    "promote_change",
    "tune_kernel",
    "network_egress",
    "execute_binary",
    "modify_bootloader",
    "access_hardware",
    "privilege_escalation",
    "delete_system_file",
    "control_desktop",
]
# Aplicar no PD uma mudança ensaiada: SEMPRE pergunta, em qualquer modo (nenhum ajuste libera), a cada vez
ALWAYS_ASK = {"promote_change"}
# Nunca liberadas sem você ver: o modo/ajuste no máximo "pergunta". Usar o mouse e o teclado pede aprovação e
# pode valer para a conversa ("Permitir nesta conversa"), mas nunca vira "permitido" pela política.
NEVER_AUTO = ALWAYS_ASK | {"control_desktop"}
CRITICAL = {"modify_bootloader", "access_hardware", "privilege_escalation", "delete_system_file"}
EXPLICIT_ONLY = {"read_secret"}   # nunca liberada por modo, só por ajuste explícito
ACTIONS = ("allow", "ask", "deny")
SEVERITY = {"allow": 0, "ask": 1, "deny": 2}
MODES = ("restrito", "perguntar", "livre")

LABELS = {
    "read": "Ler arquivos e informações",
    "read_secret": "Ler segredos (chaves SSH, senhas do navegador, chave da API)",
    "write_user_data": "Alterar arquivos do usuário (/home)",
    "write_system_config": "Alterar configuração do sistema",
    "install_package": "Instalar programas",
    "stage_change": "Ensaiar mudanças no ST (ambiente de teste isolado)",
    "promote_change": "Aplicar no sistema real (PD) uma mudança ensaiada",
    "tune_kernel": "Ajustar parâmetros do kernel (sysctl)",
    "network_egress": "Acessar a internet",
    "execute_binary": "Executar programas e comandos",
    "modify_bootloader": "Mexer no carregador de boot",
    "access_hardware": "Acesso direto a discos e hardware",
    "privilege_escalation": "Virar administrador (sudo/root)",
    "delete_system_file": "Apagar arquivos do sistema",
    "control_desktop": "Usar o computador por você (mouse, teclado e abrir arquivos)",
}
RISK = {
    "read": "baixo", "read_secret": "alto", "write_user_data": "médio", "network_egress": "médio",
    "execute_binary": "médio", "write_system_config": "alto", "install_package": "alto",
    "stage_change": "médio", "promote_change": "alto", "tune_kernel": "alto",
    "modify_bootloader": "crítico", "access_hardware": "crítico", "privilege_escalation": "crítico",
    "delete_system_file": "crítico", "control_desktop": "alto",
}
RISK_ORDER = {"baixo": 0, "médio": 1, "alto": 2, "crítico": 3}

# Arquivos que controlam a própria IA: escrita SEMPRE negada a ela
SELF_POLICY_PATHS = (
    "/etc/tupi/agent-policy.json",
    "/etc/tupi/agent-base-policy.json",
    "/etc/tupi/permission-policy.json",
    "/var/log/tupi-audit",
)
SELF_POLICY_WORDS = ("agent-policy.json", "agent-base-policy.json", "permission-policy.json", "tupi-audit",
                     "tupi-perm-mgr", "tupi-agent-exec", "perm-mgr.sock", "exec.sock")
PROTECTED_UNITS = {"tupi-perm-mgr.service", "tupi-agent-exec.service", "systemd-journald.service",
                   "systemd-journald.socket"}
SESSION_CRITICAL_UNITS = {"display-manager.service", "dbus.service", "systemd-logind.service",
                          "polkit.service", "NetworkManager.service"}

SYSTEM_DIRS = ("/etc", "/nix", "/boot", "/efi", "/usr", "/bin", "/sbin", "/lib", "/lib64", "/opt", "/srv",
               "/var", "/root", "/run/current-system", "/run/booted-system")


# ------------------------------------------------------------------- modos
def mode_table(mode):
    """Ação de cada categoria no modo, antes de qualquer ajuste."""
    if mode == "livre":
        table = {c: ("deny" if c in EXPLICIT_ONLY else "allow") for c in CATEGORIES}
    elif mode == "perguntar":
        table = {c: ("deny" if c in CRITICAL or c in EXPLICIT_ONLY else "ask") for c in CATEGORIES}
    else:
        # restrito (padrão): só leitura liberada; escrita no /home, rede, execução e ensaio no ST perguntam;
        # parâmetros do kernel e o resto: negados
        table = {c: "deny" for c in CATEGORIES}
        table.update(read="allow", write_user_data="ask", network_egress="ask", execute_binary="ask",
                     stage_change="ask")
    for c in NEVER_AUTO:
        table[c] = "ask"
    return table


def livre_expired(runtime, now=None, boot_id=None):
    livre = runtime.get("livre") or {}
    scope = livre.get("scope", "sempre")
    now = time.time() if now is None else now
    if scope == "1h" or livre.get("until"):
        return now > float(livre.get("until") or 0)
    if scope == "boot":
        return bool(boot_id) and livre.get("boot_id") != boot_id
    return False


def effective_policy(base, runtime, now=None, boot_id=None):
    """Combina: modo (NixOS ou runtime) -> overrides do NixOS -> overrides do runtime."""
    base = base or {}
    runtime = runtime or {}
    mode = base.get("mode") if base.get("mode") in MODES else "restrito"
    mode_source = "NixOS (tupi.agent.mode)"
    notes = []
    rt_mode = runtime.get("mode")
    if rt_mode in MODES:
        if rt_mode == "livre" and livre_expired(runtime, now, boot_id):
            notes.append("O modo livre expirou; voltou ao modo configurado.")
        else:
            mode = rt_mode
            mode_source = "Permissões da IA (/etc/tupi/agent-policy.json)"
    table = mode_table(mode)
    sources = {c: f"modo {mode}" for c in CATEGORIES}
    for layer, label in ((base.get("overrides"), "NixOS (tupi.agent.policyOverrides)"),
                         (runtime.get("overrides"), "ajuste em Permissões da IA")):
        for cat, action in (layer or {}).items():
            if cat in table and action in ACTIONS:
                if cat in NEVER_AUTO and action == "allow":
                    notes.append(f"\"{LABELS[cat]}\" sempre pergunta: o ajuste para permitir foi ignorado.")
                    continue
                table[cat] = action
                sources[cat] = label
    livre = None
    if mode == "livre":
        lv = runtime.get("livre") or {}
        livre = {"scope": lv.get("scope", "sempre"), "until": lv.get("until"), "since": lv.get("since")}
    return {"mode": mode, "mode_source": mode_source, "table": table, "sources": sources,
            "livre": livre, "notes": notes}


# ---------------------------------------------------------- classificação
class Classification:
    def __init__(self, tool, summary, target=""):
        self.tool, self.summary, self.target = tool, summary, target
        self.categories = set()
        self.destructive = False
        self.self_policy = False
        self.forbidden = []        # motivos de bloqueio fixo (ex.: sysctl que executa programas como root)
        self.reasons = []
        self.where = "PD"          # onde a ação acontece: PD (sistema real) ou ST (ensaio)

    def add(self, cat, reason=None):
        self.categories.add(cat)
        if reason:
            self.reasons.append(reason)

    @property
    def risk(self):
        if self.destructive or self.self_policy or self.forbidden:
            return "crítico"
        if not self.categories:
            return "baixo"
        return max((RISK[c] for c in self.categories), key=lambda x: RISK_ORDER[x])

    def to_dict(self):
        return {"tool": self.tool, "summary": self.summary, "target": self.target,
                "categories": sorted(self.categories, key=CATEGORIES.index), "risk": self.risk,
                "destructive": self.destructive, "self_policy": self.self_policy, "forbidden": self.forbidden,
                "reasons": self.reasons, "where": self.where}


def norm_path(path, home):
    p = str(path or "").strip() or "."
    if p == "~" or p.startswith("~/"):
        p = home + p[1:]
    p = p.replace("$HOME", home).replace("${HOME}", home)
    if not p.startswith("/"):
        p = os.path.join(home, p)
    return os.path.normpath(p)


SECRET_PATTERNS = [
    r"(^|/)\.ssh(/|$)", r"(^|/)\.gnupg(/|$)", r"(^|/)\.password-store(/|$)", r"(^|/)\.netrc$",
    r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", r"(^|/)\.mozilla/firefox/.*(key[34]\.db|logins\.json|cookies\.sqlite)$",
    r"(^|/)\.config/(chromium|google-chrome|BraveSoftware)/.*(Login Data|Cookies)", r"^/etc/tupi/ai-config\.json$",
    r"^/etc/(shadow|gshadow)", r"^/etc/ssh/ssh_host_.*_key$", r"^/root(/|$)", r"(^|/)\.aws/credentials$",
    r"(^|/)\.config/gh/hosts\.yml$", r"(^|/)\.docker/config\.json$", r"(^|/)\.kube/config$",
]
SECRET_WORDS = (".ssh", ".gnupg", "id_rsa", "id_ed25519", "ai-config.json", "/etc/shadow", "key4.db",
                "logins.json", ".password-store", ".netrc", ".aws/credentials")


def is_secret(path):
    return any(re.search(p, path) for p in SECRET_PATTERNS)


def is_self_policy(path):
    return any(path == p or path.startswith(p + "/") for p in SELF_POLICY_PATHS)


def in_home(path, home):
    return path == home or path.startswith(home.rstrip("/") + "/") or path.startswith("/tmp/") or path == "/tmp"


def classify_write(c, path, home, deleting=False, recursive=False):
    """Categoria de escrever/apagar um caminho."""
    if is_self_policy(path):
        c.self_policy = True
        c.add("write_system_config", f"{path} controla a própria IA")
        return
    if is_secret(path):
        c.add("read_secret", f"{path} é um arquivo de segredos")
    if path.startswith("/dev/") and re.match(r"/dev/(sd|hd|vd|nvme|mmcblk|xvd|loop|md|dm-|mapper)", path):
        c.add("access_hardware", f"escrita direta no dispositivo {path}")
        c.destructive = True
        return
    if path.startswith(("/boot", "/efi")):
        c.add("modify_bootloader", f"{path} é do carregador de boot")
        return
    if path.startswith("/proc/sys/"):
        classify_sysctl(c, path[len("/proc/sys/"):].replace("/", "."), None)
        c.reasons.append("para mudar parâmetros do kernel use set_kernel_param (guarda o valor para Desfazer)")
        return
    if path.startswith(("/sys", "/proc/sys")):
        c.add("access_hardware", f"{path} é interface do kernel")
        return
    if in_home(path, home):
        c.add("write_user_data")
        if deleting and recursive and path.rstrip("/") in (home.rstrip("/"), "/tmp"):
            c.destructive = True
            c.reasons.append("apagar a pasta pessoal inteira")
        return
    if path in ("/", "/dev/null"):
        if path == "/":
            c.add("delete_system_file" if deleting else "write_system_config", "raiz do sistema")
            c.destructive = True
        return
    if deleting:
        c.add("delete_system_file", f"{path} é arquivo do sistema")
        if recursive:
            c.destructive = True
    else:
        c.add("write_system_config", f"{path} fica fora da pasta pessoal")


# ------------------------------------------------- parâmetros do kernel (sysctl)
SYSCTL_NAME = re.compile(r"^[a-z0-9_]+(\.[A-Za-z0-9_-]+)+$")
SYSCTL_VALUE = re.compile(r"^[A-Za-z0-9 ._:,/+-]{1,200}$")
# Sempre negados (qualquer modo): executam programas como root ou não têm volta até reiniciar
SYSCTL_FORBIDDEN = {
    "kernel.core_pattern": "pode fazer o kernel executar um programa como root",
    "kernel.core_pipe_limit": "ligado ao core_pattern (execução de programas pelo kernel)",
    "kernel.modprobe": "troca o programa que o kernel executa como root",
    "kernel.poweroff_cmd": "troca o programa que o kernel executa como root",
    "kernel.hotplug": "troca o programa que o kernel executa como root",
    "kernel.modules_disabled": "irreversível até reiniciar (não há Desfazer)",
    "kernel.kexec_load_disabled": "irreversível até reiniciar (não há Desfazer)",
}
SYSCTL_FORBIDDEN_PREFIX = ("kernel.usermodehelper.",)
# Arriscados: pedem confirmação extra (como comandos destrutivos), mesmo no modo livre
SYSCTL_RISKY_PREFIX = ("kernel.panic", "kernel.hung_task_panic", "kernel.softlockup_panic",
                       "kernel.hardlockup_panic", "kernel.unknown_nmi_panic", "vm.panic_on_oom", "fs.protected_")
SYSCTL_RISKY = {
    "kernel.sysrq": "permite comandos de emergência pelo teclado (reiniciar, matar processos)",
    "kernel.randomize_va_space": "muda uma proteção de segurança da memória (ASLR)",
    "kernel.kptr_restrict": "expõe endereços do kernel",
    "kernel.dmesg_restrict": "expõe o log do kernel",
    "kernel.perf_event_paranoid": "muda uma proteção de segurança",
    "kernel.yama.ptrace_scope": "muda quem pode inspecionar outros processos",
    "kernel.unprivileged_bpf_disabled": "muda uma proteção de segurança",
    "user.max_user_namespaces": "valores baixos quebram containers e navegadores",
    "fs.suid_dumpable": "pode expor memória de programas privilegiados",
    "vm.min_free_kbytes": "valores errados travam o sistema por falta de memória",
    "vm.nr_hugepages": "reserva memória que deixa de ficar disponível",
    "vm.overcommit_kbytes": "muda como o kernel promete memória aos programas",
}
SYSCTL_NO_UNDO = {"vm.drop_caches", "vm.compact_memory"}   # ações, não estados


def sysctl_risk(name, value):
    """-> ("proibido" | "arriscado" | "normal", motivo)."""
    v = str(value).strip() if value is not None else None
    if name in SYSCTL_FORBIDDEN:
        return "proibido", SYSCTL_FORBIDDEN[name]
    if name.startswith(SYSCTL_FORBIDDEN_PREFIX):
        return "proibido", "troca programas que o kernel executa como root"
    if name == "kernel.yama.ptrace_scope" and v == "3":
        return "proibido", "o valor 3 é irreversível até reiniciar"
    if name == "kernel.unprivileged_bpf_disabled" and v == "1":
        return "proibido", "o valor 1 é irreversível até reiniciar"
    if name.startswith(SYSCTL_RISKY_PREFIX):
        return "arriscado", "pode reiniciar/travar o computador ou enfraquecer proteções"
    if name in SYSCTL_RISKY:
        return "arriscado", SYSCTL_RISKY[name]
    if name == "vm.overcommit_memory" and v not in (None, "0"):
        return "arriscado", "valor extremo: 1 deixa o kernel prometer memória sem limite; 2 faz programas falharem"
    if name == "vm.overcommit_ratio" and v is not None and not (v.isdigit() and 20 <= int(v) <= 100):
        return "arriscado", "valor extremo para a porcentagem de memória prometida"
    if name == "vm.max_map_count" and v is not None and v.isdigit() and int(v) < 65530:
        return "arriscado", "valores baixos fazem programas grandes falharem"
    if name in ("vm.overcommit_memory", "vm.overcommit_ratio", "vm.max_map_count") and v is None:
        return "arriscado", "o valor pode ser extremo"
    return "normal", ""


def classify_sysctl(c, name, value):
    c.add("tune_kernel", f"parâmetro do kernel {name}")
    if not SYSCTL_NAME.match(name or "") or ".." in name:
        c.forbidden.append(f"nome de parâmetro inválido: {name!r}")
        return
    if value is not None and not SYSCTL_VALUE.match(str(value)):
        c.forbidden.append(f"valor inválido para {name}: {value!r}")
        return
    level, why = sysctl_risk(name, value)
    if level == "proibido":
        c.forbidden.append(f"{name}: {why}")
    elif level == "arriscado":
        c.destructive = True
        c.reasons.append(f"arriscado: {name} — {why}")


def sysctl_assignments(args):
    """Argumentos do comando sysctl -> [(nome, valor)] das escritas (sysctl -w a=b, sysctl a=b)."""
    out = []
    for a in args:
        if "=" in a and not a.startswith("-"):
            k, v = a.split("=", 1)
            out.append((k.strip().replace("/", "."), v.strip()))
    return out


# Palavras que, numa mudança de configuração, mexeriam no controle da própria IA (sempre negado)
CONFIG_SELF_WORDS = ("tupi.agent", "tupi.permissionManager", "permissionManager", "tupi-perm-mgr", "tupi-agent",
                     "tupi-audit", "agent-policy", "permission-policy", "agent-base-policy", "/etc/tupi",
                     "tupi.st", "auditLogPath", "tupi-ia")
CONFIG_BOOT_WORDS = ("boot.loader", "boot.initrd", "fileSystems", "swapDevices", "boot.kernelPackages",
                     "boot.kernelParams", "boot.blacklistedKernelModules")
PKG_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]{0,99}$")


def classify_config_change(c, change):
    """Mudança de configuração NixOS (ensaio no ST ou promoção para o PD)."""
    change = change if isinstance(change, dict) else {}
    nix = str(change.get("nix") or "")
    for p in list(change.get("pacotes") or []) + list(change.get("remover_pacotes") or []):
        if not PKG_NAME.match(str(p)):
            c.forbidden.append(f"nome de pacote inválido: {p!r}")
    for name, value in (change.get("sysctl") or {}).items():
        level, why = sysctl_risk(str(name), value)
        if not SYSCTL_NAME.match(str(name)) or level == "proibido":
            c.forbidden.append(f"{name}: {why or 'nome inválido'}")
        elif level == "arriscado":
            c.destructive = True
            c.reasons.append(f"arriscado: {name} — {why}")
    if change.get("sysctl") or change.get("parametros_kernel"):
        c.reasons.append("parâmetros do kernel NÃO podem ser ensaiados no ST: o container usa o mesmo kernel do PD")
    for p in change.get("parametros_kernel") or []:
        if not re.fullmatch(r"[A-Za-z0-9_.,:=/+-]{1,120}", str(p)):
            c.forbidden.append(f"parâmetro de boot inválido: {p!r}")
    low = nix.lower()
    if any(w.lower() in low for w in CONFIG_SELF_WORDS):
        c.self_policy = True
        c.reasons.append("a mudança mexe nas configurações da própria IA, do Permission Manager ou da auditoria")
    hits = [w for w in CONFIG_BOOT_WORDS if w.lower() in low]
    if hits or change.get("parametros_kernel"):
        c.destructive = True
        c.reasons.append("mexe em boot/discos/hardware (" + ", ".join(hits or ["boot.kernelParams"]) +
                         "): um erro pode impedir o boot — confirme de novo (as gerações anteriores continuam no menu de boot)")
    if len(nix) > 20000:
        c.forbidden.append("trecho de configuração grande demais")


def change_summary(change):
    change = change if isinstance(change, dict) else {}
    parts = []
    if change.get("pacotes"):
        parts.append("instalar " + ", ".join(map(str, change["pacotes"])))
    if change.get("remover_pacotes"):
        parts.append("remover " + ", ".join(map(str, change["remover_pacotes"])))
    if change.get("sysctl"):
        parts.append("sysctl " + ", ".join(f"{k}={v}" for k, v in change["sysctl"].items()))
    if change.get("parametros_kernel"):
        parts.append("parâmetros de boot " + " ".join(map(str, change["parametros_kernel"])))
    if change.get("nix"):
        parts.append("trecho de configuração NixOS")
    return "; ".join(parts) or "nenhuma mudança"


# programas
READ_ONLY = {
    "ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "stat", "wc", "du", "df",
    "free", "uptime", "ps", "pgrep", "whoami", "id", "date", "uname", "echo", "printf", "pwd", "which",
    "type", "file", "tree", "sort", "uniq", "cut", "tr", "diff", "cmp", "md5sum", "sha1sum", "sha256sum",
    "basename", "dirname", "realpath", "readlink", "printenv", "hostname", "lsblk", "lscpu", "lsusb", "lspci",
    "nproc", "journalctl", "ss", "jq", "column", "nl", "tac", "rev", "xxd", "od", "strings", "getent",
    "groups", "w", "who", "true", "false", "test", "[", "sleep", "seq", "fc-list", "locale", "cal", "bc",
    "neofetch", "fastfetch", "htop", "btop", "top", "vmstat", "iostat", "sensors", "lsof", "findmnt",
    "blkid", "env", "awk", "sed", "find", "git", "systemctl", "nix", "ip", "man", "whatis", "apropos",
    "xdg-user-dir", "tldr",
}
NETWORK = {"curl", "wget", "ssh", "scp", "sftp", "ping", "ping6", "nc", "ncat", "netcat", "telnet", "nmap",
           "dig", "nslookup", "host", "ftp", "aria2c", "yt-dlp", "traceroute", "mtr", "whois", "rsync"}
PRIV = {"sudo", "su", "doas", "pkexec", "run0", "chroot", "nsenter", "unshare", "setpriv", "capsh"}
HARDWARE = {"dd", "mkfs", "fdisk", "sfdisk", "cfdisk", "parted", "gdisk", "sgdisk", "wipefs", "hdparm",
            "mount", "umount", "losetup", "cryptsetup", "modprobe", "rmmod", "insmod", "setpci", "flashrom",
            "mkswap", "swapon", "swapoff", "blkdiscard", "badblocks", "e2fsck", "fsck", "btrfs", "zpool", "zfs",
            "nvme", "smartctl", "dmsetup", "lvcreate", "lvremove", "vgremove", "pvremove"}
HARDWARE_DESTRUCTIVE = {"dd", "fdisk", "sfdisk", "parted", "gdisk", "sgdisk", "wipefs", "mkswap",
                        "blkdiscard", "badblocks", "lvremove", "vgremove", "pvremove"}
BOOT = {"grub-install", "grub-mkconfig", "update-grub", "bootctl", "efibootmgr", "kexec"}
PKG = {"nix-env", "apt", "apt-get", "dnf", "yum", "pacman", "zypper", "snap", "flatpak", "brew", "emerge"}
POWER = {"shutdown", "reboot", "poweroff", "halt", "init", "telinit"}
DELETE = {"rm", "rmdir", "unlink", "shred"}
WRITE_TARGET_LAST = {"cp", "mv", "ln", "install", "rsync"}
WRITE_TARGET_ALL = {"touch", "mkdir", "tee", "truncate", "chmod", "chown", "chgrp", "setfacl", "chattr",
                    "mkfifo", "mknod"}
WRAPPERS = {"env", "nice", "nohup", "time", "timeout", "ionice", "stdbuf", "command", "builtin", "exec",
            "taskset", "chrt"}
INTERPRETERS = {"bash", "sh", "zsh", "dash", "fish", "ksh"}
OPERATORS = {";", "&&", "||", "|", "&", "(", ")", "|&", ";;", "\n"}

DESTRUCTIVE_PATTERNS = [
    (r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r|(-[a-zA-Z]*r[a-zA-Z]*|--recursive)\s+(-[a-zA-Z]*f|--force)|"
     r"(-[a-zA-Z]*f[a-zA-Z]*|--force)\s+(-[a-zA-Z]*r|--recursive))", "rm -rf (apaga pastas inteiras sem perguntar)"),
    (r"\bdd\b[^|;&]*\bof=", "dd (grava direto em arquivo/disco)"),
    (r"\bmkfs(\.\w+)?\b", "mkfs (formata um disco/partição)"),
    (r"\bwipefs\b", "wipefs (apaga assinaturas de disco)"),
    (r"\bshred\b", "shred (destrói arquivos)"),
    (r"\bch(mod|own|grp)\s+(-[a-zA-Z]*R[a-zA-Z]*|--recursive)\b[^|;&]*\s/(\s|$|\*|bin|boot|etc|home|lib|nix|usr|var)",
     "permissões recursivas na raiz do sistema"),
    (r"\bfind\b[^|;&]*\s-delete\b", "find -delete (apaga em massa)"),
    (r">\s*/dev/(sd|hd|vd|nvme|mmcblk)", "escrita direta em disco"),
    (r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", "fork bomb"),
    (r"\bmv\b[^|;&]*\s/dev/null\b", "mover arquivos para /dev/null"),
    (r"\btruncate\s+(-s\s*0|--size[= ]0)", "truncate (zera arquivos)"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*[fdx]", "git clean (apaga arquivos não versionados)"),
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard (descarta alterações)"),
    (r"\bkill(all)?\s+-(9|KILL)\s+(-1|1)\b", "matar todos os processos"),
    (r"\b(shutdown|reboot|poweroff|halt)\b", "desligar/reiniciar o computador"),
]


def tokenize(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def split_commands(tokens):
    """Separa em comandos simples e coleta redirecionamentos de escrita/leitura."""
    cmds, cur, writes, reads = [], [], [], []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in (">&", "<&", "<<", "<<<"):   # 2>&1, heredoc: não grava arquivo
            i += 2
            continue
        if t in (">", ">>", ">|", "&>", "&>>", "1>", "2>", "2>>") or re.fullmatch(r"[0-9]*>{1,2}", t):
            if i + 1 < len(tokens):
                if tokens[i + 1] not in ("&", "&1", "&2"):
                    writes.append(tokens[i + 1])
                i += 2
                continue
        if t == "<" and i + 1 < len(tokens):
            reads.append(tokens[i + 1])
            i += 2
            continue
        if t in OPERATORS or set(t) <= set(";&|()"):
            if cur:
                cmds.append(cur)
            cur = []
        else:
            cur.append(t)
        i += 1
    if cur:
        cmds.append(cur)
    return cmds, writes, reads


def classify_simple(c, argv, home, depth):
    """Classifica um comando simples (já sem operadores)."""
    while argv and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", argv[0]):
        argv = argv[1:]   # VAR=valor cmd
    if not argv:
        return
    prog = os.path.basename(argv[0])
    args = argv[1:]
    if prog in PRIV:
        c.add("privilege_escalation", f"{prog} pede privilégio de administrador")
        rest = [a for a in args if not a.startswith("-")] if prog in ("sudo", "doas", "run0") else []
        if prog == "su" and "-c" in args:
            i = args.index("-c")
            rest = tokenize(args[i + 1]) if i + 1 < len(args) else []
        if rest:
            classify_simple(c, rest, home, depth + 1)
        return
    if prog in WRAPPERS:
        rest = args
        if prog == "timeout" and rest:
            rest = rest[1:]
        rest = [a for a in rest if not (a.startswith("-") or re.fullmatch(r"[A-Za-z_]\w*=.*", a))]
        if rest:
            classify_simple(c, rest, home, depth + 1)
        return
    if prog in INTERPRETERS and "-c" in args and depth < 4:
        i = args.index("-c")
        if i + 1 < len(args):
            classify_shell(c, args[i + 1], home, depth + 1)
            return
    if prog == "xargs":
        c.add("execute_binary", "xargs executa comandos com a entrada")
        rest = [a for a in args if not a.startswith("-")]
        if rest:
            classify_simple(c, rest, home, depth + 1)
        return
    if prog in BOOT:
        c.add("modify_bootloader", f"{prog} altera o boot")
        return
    if prog.startswith("mkfs") or prog in HARDWARE:
        c.add("access_hardware", f"{prog} mexe em discos/hardware")
        if prog in HARDWARE_DESTRUCTIVE or prog.startswith("mkfs"):
            c.destructive = True
        if prog == "dd":
            for a in args:
                if a.startswith("of="):
                    classify_write(c, norm_path(a[3:], home), home)
        return
    if prog == "nixos-rebuild":
        c.add("write_system_config", "nixos-rebuild aplica uma nova configuração do sistema")
        c.add("modify_bootloader", "nixos-rebuild atualiza as entradas de boot")
        return
    if prog == "sysctl":
        writes = sysctl_assignments(args)
        if writes:
            for k, v in writes:
                classify_sysctl(c, k, v)
            c.reasons.append("para mudar parâmetros do kernel use set_kernel_param (guarda o valor para Desfazer)")
        elif any(a in ("-p", "--load", "--system", "-f") or a.startswith(("--load=", "-p")) for a in args):
            c.add("tune_kernel", "sysctl --system/-p recarrega vários parâmetros do kernel")
            c.destructive = True
            c.reasons.append("arriscado: recarrega vários parâmetros do kernel de uma vez")
        else:
            c.add("read")
        return
    if prog in PKG or (prog == "nix" and args[:2] in (["profile", "install"], ["profile", "add"])):
        c.add("install_package", f"{prog} instala programas de forma imperativa (use install_package)")
        c.add("network_egress")
        return
    if prog in POWER or (prog == "systemctl" and args and args[0] in ("reboot", "poweroff", "halt", "suspend",
                                                                     "hibernate", "kexec", "rescue", "emergency")):
        c.add("write_system_config", "desligar/reiniciar o sistema")
        c.destructive = True
        return
    if prog in NETWORK:
        c.add("network_egress", f"{prog} acessa a rede")
        if prog in ("scp", "rsync", "sftp"):
            c.add("write_user_data")
        if prog in ("curl", "wget"):
            for flag in ("-o", "-O", "--output", "--output-document"):
                if flag in args:
                    i = args.index(flag)
                    if i + 1 < len(args):
                        classify_write(c, norm_path(args[i + 1], home), home)
        return
    if prog in DELETE:
        recursive = any(re.fullmatch(r"-[a-zA-Z]*[rR][a-zA-Z]*", a) or a == "--recursive" for a in args)
        targets = [a for a in args if not a.startswith("-")]
        if not targets:
            c.add("write_user_data")
        for t in targets:
            classify_write(c, norm_path(t, home), home, deleting=True, recursive=recursive)
        return
    if prog in WRITE_TARGET_LAST:
        targets = [a for a in args if not a.startswith("-")]
        if prog == "mv":
            for t in targets[:-1]:
                classify_write(c, norm_path(t, home), home, deleting=True)
        if targets:
            classify_write(c, norm_path(targets[-1], home), home)
        for t in targets[:-1]:
            p = norm_path(t, home)
            if is_secret(p):
                c.add("read_secret", f"{p} é um arquivo de segredos")
        return
    if prog in WRITE_TARGET_ALL:
        targets = [a for a in args if not a.startswith("-") and not re.fullmatch(r"[0-7]{3,4}|[ugoa]*[+-=][rwxXst]+|\w+(:\w*)?", a)]
        recursive = any(a in ("-R", "--recursive") or re.fullmatch(r"-[a-zA-Z]*R", a) for a in args)
        if prog in ("chown", "chgrp") and any(not a.startswith("-") for a in args):
            targets = [a for a in args if not a.startswith("-")][1:]
        for t in targets or ["."]:
            classify_write(c, norm_path(t, home), home, recursive=recursive)
            if recursive and norm_path(t, home) in ("/",) + SYSTEM_DIRS:
                c.destructive = True
        return
    if prog in ("kill", "pkill", "killall"):
        c.add("execute_binary", "encerra processos")
        return
    if prog == "systemctl":
        verb = next((a for a in args if not a.startswith("-")), "status")
        if verb in ("status", "show", "is-active", "is-enabled", "is-failed", "list-units", "list-unit-files",
                    "list-timers", "cat", "list-dependencies", "help"):
            c.add("read")
        else:
            c.add("write_system_config", f"systemctl {verb} muda serviços do sistema")
            if "--user" in args:
                c.categories.discard("write_system_config")
                c.add("execute_binary")
        return
    if prog == "git":
        verb = next((a for a in args if not a.startswith("-")), "")
        if verb in ("clone", "pull", "push", "fetch", "ls-remote", "submodule"):
            c.add("network_egress", f"git {verb} acessa a rede")
            c.add("write_user_data")
        elif verb in ("status", "log", "diff", "show", "branch", "rev-parse", "ls-files", "blame", "remote",
                      "describe", "shortlog", "grep", "tag", "config", ""):
            c.add("read")
        else:
            c.add("write_user_data", f"git {verb} altera o repositório")
        return
    if prog == "nix":
        verb = args[0] if args else ""
        if verb in ("eval", "search", "flake", "path-info", "show-derivation", "why-depends", "log", "hash"):
            c.add("read")
            if verb in ("search", "flake"):
                c.add("network_egress", "nix pode baixar do cache/GitHub")
        else:
            c.add("execute_binary", f"nix {verb}")
            c.add("network_egress", "nix pode baixar pacotes")
        return
    if prog == "find" and any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf",
                                    "-fls") for a in args):
        c.add("execute_binary", "find com -exec/-delete")
        if "-delete" in args:
            targets = [a for a in args if not a.startswith("-")][:1] or ["."]
            for t in targets:
                classify_write(c, norm_path(t, home), home, deleting=True, recursive=True)
        return
    if prog == "sed" and any(a == "-i" or a.startswith("-i") or a == "--in-place" for a in args):
        files = [a for a in args if not a.startswith("-")][1:]
        for f in files or ["."]:
            classify_write(c, norm_path(f, home), home)
        return
    if prog == "awk" and re.search(r"system\s*\(|>\s*\"", " ".join(args)):
        c.add("execute_binary", "awk com system()/escrita")
        return
    if prog in ("python3", "python", "node", "perl", "ruby", "php", "lua"):
        c.add("execute_binary", f"{prog} executa código arbitrário")
        return
    if prog == "ip":
        verb = args[0] if args else ""
        sub = args[1] if len(args) > 1 else "show"
        if sub in ("show", "list", "ls", "") or verb in ("-br", "-c", "-4", "-6"):
            c.add("read")
        else:
            c.add("write_system_config", "ip altera a rede")
        return
    if prog in READ_ONLY:
        c.add("read")
        for a in args:
            if not a.startswith("-") and ("/" in a or a.startswith("~") or a.startswith(".")):
                p = norm_path(a, home)
                if is_secret(p):
                    c.add("read_secret", f"{p} é um arquivo de segredos")
        return
    c.add("execute_binary", f"executa o programa {prog}")


def classify_shell(c, command, home, depth=0):
    raw = command
    if re.search(r"\$\(|`|<\(|>\(|\beval\b|\bsource\b|^\s*\.\s", raw):
        c.add("execute_binary", "o comando gera/roda outros comandos (substituição, eval ou source)")
    try:
        tokens = tokenize(raw)
    except ValueError:
        c.add("execute_binary", "não foi possível analisar o comando (aspas abertas?)")
        return
    cmds, writes, reads = split_commands(tokens)
    for w in writes:
        if w in ("/dev/null", "/dev/stdout", "/dev/stderr", "/dev/tty"):
            continue
        classify_write(c, norm_path(w, home), home)
    for r in reads:
        p = norm_path(r, home)
        if is_secret(p):
            c.add("read_secret", f"{p} é um arquivo de segredos")
    for argv in cmds:
        classify_simple(c, argv, home, depth)


# ------------------------------------------------- usar o computador (mouse/teclado)
COMPUTER_ACTIONS = ("screenshot", "move", "click", "double_click", "right_click", "middle_click", "drag", "scroll",
                    "type", "key", "wait")
COMPUTER_READ = {"screenshot", "wait"}          # só olhar/esperar: leitura
KEY_TOKEN = re.compile(r"^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$")
# sair da sessão gráfica (terminal de texto), derrubar o X ou o "três dedos": ninguém acompanharia
FORBIDDEN_KEYS = re.compile(r"(?i)(^|\+)(ctrl|control)\+(alt|meta)\+(f\d{1,2}|backspace|delete|del)$|sys_?req|"
                            r"(^|\+)alt\+(ctrl|control)\+(f\d{1,2}|backspace|delete|del)$")
# o que vale como "comando perigoso" quando a IA DIGITA um texto (ex.: num terminal)
TYPED_RISK = {"privilege_escalation", "delete_system_file", "modify_bootloader", "access_hardware",
              "write_system_config", "install_package", "read_secret", "tune_kernel"}
TYPED_SELF = re.compile(r"\btupi-agent\b(?!-)|\btupi-perm|/etc/tupi/|\bpaje\s+(provedor|conexao)\b")
DIRECTIONS = {"up": "cima", "down": "baixo", "left": "a esquerda", "right": "a direita"}


def _xy(args, kx="x", ky="y"):
    try:
        return int(args.get(kx)), int(args.get(ky))
    except (TypeError, ValueError):
        return None


def classify_computer(args, home):
    action = str(args.get("action", "")).strip()
    p, p2 = _xy(args), _xy(args, "x2", "y2")
    where = f" em ({p[0]}, {p[1]})" if p else ""
    summary = {"screenshot": "Ver a tela (print para a IA)", "move": f"Mover o mouse{where}",
               "click": f"Clicar{where}", "double_click": f"Clicar duas vezes{where}",
               "right_click": f"Clicar com o botão direito{where}", "middle_click": f"Clicar com o botão do meio{where}",
               "drag": f"Arrastar de ({p[0] if p else '?'}, {p[1] if p else '?'}) até "
                       f"({p2[0] if p2 else '?'}, {p2[1] if p2 else '?'})",
               "scroll": f"Rolar para {DIRECTIONS.get(str(args.get('direction')), 'baixo')}{where}",
               "type": "Digitar: " + repr(str(args.get("text", ""))[:160]),
               "key": f"Apertar as teclas {str(args.get('keys', ''))[:80]}",
               "wait": f"Esperar {args.get('seconds', 1)} s"}.get(action, f"Ação de tela desconhecida: {action}")
    c = Classification("use_computer", summary, action)
    if action not in COMPUTER_ACTIONS:
        c.add("control_desktop")
        c.forbidden.append(f"ação '{action}' não existe (use: {', '.join(COMPUTER_ACTIONS)})")
        return c
    if action in COMPUTER_READ:
        c.add("read")
        return c
    c.add("control_desktop", "mexe no mouse/teclado na sua tela: a IA pode fazer o que você faria; acompanhe")
    if action in ("move", "click", "double_click", "right_click", "middle_click", "scroll", "drag") and not p:
        c.forbidden.append("faltam as coordenadas x e y")
    if action == "drag" and not p2:
        c.forbidden.append("faltam as coordenadas x2 e y2 do destino")
    if action == "key":
        keys = str(args.get("keys", "")).split()
        if not keys or len(keys) > 10 or not all(KEY_TOKEN.match(k) for k in keys):
            c.forbidden.append("teclas inválidas (ex.: ctrl+s, Return, alt+Tab)")
        elif any(FORBIDDEN_KEYS.search(k) for k in keys):
            c.forbidden.append("trocar para um terminal de texto, reiniciar a interface ou Ctrl+Alt+Del: a IA "
                               "sairia da tela que você está acompanhando")
    if action == "type":
        text = str(args.get("text", ""))
        if not text:
            c.forbidden.append("texto vazio")
        if len(text) > 4000:
            c.forbidden.append("texto longo demais (máx. 4000 caracteres)")
        # o texto pode ir parar num terminal: vale a mesma régua de um comando
        tmp = Classification("run_command", "")
        try:
            classify_shell(tmp, text, home)
        except Exception:
            pass
        for cat in sorted(tmp.categories & TYPED_RISK):
            c.add(cat, f"o texto digitado parece um comando que exige: {LABELS[cat].lower()}")
        low = text.lower()
        if any(w in low for w in SELF_POLICY_WORDS) or TYPED_SELF.search(low):
            c.self_policy = True
            c.reasons.append("o texto digitado mexe na política, nas aprovações ou nas chaves da IA")
        if any(w in text for w in SECRET_WORDS):
            c.add("read_secret", "o texto digitado menciona arquivos de segredos")
        for pat, why in DESTRUCTIVE_PATTERNS:
            if re.search(pat, text):
                c.destructive = True
                c.reasons.append(f"destrutivo: {why} (no texto digitado)")
                break
    return c


def classify(tool, args, home="/root"):
    """Chamada de ferramenta -> Classification (categorias, alvo, risco, destrutivo, auto-política)."""
    args = args or {}
    home = (home or "/root").rstrip("/") or "/root"

    if tool in ("read_file", "list_dir"):
        path = norm_path(args.get("path", "."), home)
        verb = "Ler o arquivo" if tool == "read_file" else "Listar a pasta"
        c = Classification(tool, f"{verb} {path}", path)
        if is_secret(path):
            c.add("read_secret", f"{path} contém segredos")
        else:
            c.add("read")
        return finish(c)

    if tool == "write_file":
        path = norm_path(args.get("path", ""), home)
        mode = "Acrescentar em" if args.get("mode") == "append" else "Gravar o arquivo"
        c = Classification(tool, f"{mode} {path}", path)
        classify_write(c, path, home)
        return finish(c)

    if tool == "system_info":
        c = Classification(tool, "Ver CPU, memória, disco e processos")
        c.add("read")
        return finish(c)

    if tool == "take_screenshot":
        c = Classification(tool, "Tirar um print da tela")
        c.add("read")
        return finish(c)

    if tool == "open_app":
        app = str(args.get("app", ""))
        c = Classification(tool, f"Abrir o aplicativo {app}", app)
        c.add("execute_binary", "abre um programa")
        return finish(c)

    if tool == "use_computer":
        return finish(classify_computer(args, home))

    if tool == "open_file":
        path = norm_path(args.get("path", ""), home)
        c = Classification(tool, f"Abrir {path} no programa padrão", path)
        if is_secret(path):
            c.add("read_secret", f"{path} contém segredos")
        c.add("control_desktop", "abre o arquivo na sua tela, no programa padrão")
        return finish(c)

    if tool == "manage_service":
        name = str(args.get("name", ""))
        unit = name if "." in name else name + ".service"
        action = str(args.get("action", "status"))
        c = Classification(tool, f"Serviço {unit}: {action}", unit)
        if action == "status":
            c.add("read")
        else:
            c.add("write_system_config", f"{action} no serviço {unit}")
            if unit in PROTECTED_UNITS:
                c.self_policy = True
                c.reasons.append(f"{unit} faz parte do controle/auditoria da IA")
            if unit in SESSION_CRITICAL_UNITS and action in ("stop", "restart"):
                c.destructive = True
                c.reasons.append(f"{action} em {unit} derruba a sessão gráfica ou a rede")
        return finish(c)

    if tool == "install_package":
        name = str(args.get("name", ""))
        c = Classification(tool, f"Instalar o pacote {name} (na configuração NixOS)", name)
        c.add("install_package", "muda a configuração do sistema e baixa programas")
        return finish(c)

    if tool == "set_kernel_param":
        name, value = str(args.get("name", "")).strip(), str(args.get("value", "")).strip()
        c = Classification(tool, f"Mudar o parâmetro do kernel {name} para {value} (agora, no PD; volta ao "
                                 f"normal ao reiniciar)", name)
        classify_sysctl(c, name, value)
        return finish(c)

    if tool == "st_run":
        cmd = str(args.get("command", ""))
        c = Classification(tool, f"Executar no ST (ensaio): {cmd}", cmd)
        c.where = "ST"
        c.add("stage_change", "roda dentro do ST: container isolado, sem a sua pasta pessoal e sem rede")
        if touches_kernel(cmd, home):
            c.reasons.append("parâmetro do kernel: o ST usa o mesmo kernel do PD, então isso não pode ser "
                             "ensaiado no container")
        return finish(c)

    if tool == "st_stage_change":
        change = {k: args.get(k) for k in ("pacotes", "remover_pacotes", "sysctl", "parametros_kernel", "nix")
                  if args.get(k)}
        desc = str(args.get("descricao") or "").strip()
        c = Classification(tool, f"Ensaiar no ST: {desc or change_summary(change)}", change_summary(change))
        c.where = "ST"
        c.add("stage_change", "constrói a configuração com a mudança e a ativa no ST (não mexe no PD)")
        classify_config_change(c, change)
        # no ST nada disso afeta o PD: o risco de boot/sysctl é cobrado na promoção, não no ensaio
        c.destructive = False
        return finish(c)

    if tool == "st_diff":
        c = Classification(tool, "Ver o que muda entre o PD e o ensaio do ST")
        c.where = "ST"
        c.add("read")
        return finish(c)

    if tool == "st_discard":
        c = Classification(tool, "Descartar a mudança em ensaio no ST")
        c.where = "ST"
        c.add("stage_change", "desliga o ST e apaga a cópia de ensaio (o PD não muda)")
        return finish(c)

    if tool == "st_promote":
        # o executor injeta aqui a mudança REALMENTE ensaiada (os argumentos da IA são ignorados)
        change = args.get("mudanca") if isinstance(args.get("mudanca"), dict) else {}
        desc = str(args.get("descricao") or "").strip()
        c = Classification(tool, f"Aplicar no PD (sistema real) a mudança ensaiada: {desc or change_summary(change)}",
                           change_summary(change))
        c.add("promote_change", "aplica no sistema real; antes cria ponto de retorno (geração NixOS e snapshot)")
        classify_config_change(c, change)
        return finish(c)

    if tool == "run_command":
        cmd = str(args.get("command", ""))
        c = Classification(tool, f"Executar: {cmd}", cmd)
        if not cmd.strip():
            c.add("execute_binary", "comando vazio")
            return finish(c)
        classify_shell(c, cmd, home)
        if not c.categories:
            c.add("execute_binary")
        low = cmd.lower()
        if not c.categories <= {"read"} and any(w in low for w in SELF_POLICY_WORDS):
            c.self_policy = True
            c.reasons.append("o comando mexe na política, no executor ou no log de auditoria da IA")
        if any(w in cmd for w in SECRET_WORDS):
            c.add("read_secret", "o comando menciona arquivos de segredos")
        for pat, why in DESTRUCTIVE_PATTERNS:
            if re.search(pat, cmd):
                c.destructive = True
                c.reasons.append(f"destrutivo: {why}")
                break
        return finish(c)

    c = Classification(tool, f"Ferramenta desconhecida: {tool}")
    c.add("execute_binary", "ferramenta desconhecida")
    c.self_policy = True   # nega
    c.reasons.append("ferramenta não existe")
    return finish(c)


def finish(c):
    # "read" junto de outra categoria não acrescenta nada
    if len(c.categories) > 1:
        c.categories.discard("read")
    return c


def touches_kernel(cmd, home="/root"):
    """O comando tenta mudar parâmetros do kernel (sysctl -w, escrita em /proc/sys)?"""
    tmp = Classification("st_run", "")
    try:
        classify_shell(tmp, str(cmd), home)
    except Exception:
        return False
    return "tune_kernel" in tmp.categories


# ----------------------------------------------------------------- decisão
def decide(classification, policy):
    """-> (decision, reason). decision: allow | ask | deny."""
    c = classification
    if c.self_policy:
        return "deny", ("A IA não pode alterar a própria política de permissões, o executor nem o log de "
                        "auditoria (regra fixa, vale em qualquer modo).")
    if c.forbidden:
        return "deny", "Bloqueado sempre (regra fixa, vale em qualquer modo): " + "; ".join(c.forbidden) + "."
    table = policy["table"]
    actions = {cat: table.get(cat, "deny") for cat in c.categories}
    for cat in c.categories & NEVER_AUTO:
        if actions[cat] == "allow":
            actions[cat] = "ask"
    worst = max(actions.values(), key=lambda a: SEVERITY[a]) if actions else "deny"
    if worst == "deny":
        denied = [cat for cat, a in actions.items() if a == "deny"]
        names = ", ".join(LABELS[d].lower() for d in denied)
        return "deny", f"Negado no modo {policy['mode']}: {names}."
    if c.destructive:
        return "ask", ("Ação arriscada/destrutiva: exige confirmação extra (vale até no modo livre)." +
                       (" " + c.reasons[-1] if c.reasons and c.reasons[-1].startswith("arriscado") else ""))
    if c.categories & ALWAYS_ASK:
        return "ask", "Aplicar no sistema real (PD) sempre pede a sua aprovação, em qualquer modo."
    if "control_desktop" in c.categories:
        return "ask", ("Usar o mouse e o teclado por você sempre pede a sua aprovação. \"Permitir nesta conversa\" "
                       "vale até você mexer no mouse, clicar em Devolver o controle ou ficar 10 minutos sem uso.")
    if worst == "ask":
        asked = [cat for cat, a in actions.items() if a == "ask"]
        return "ask", "Precisa da sua aprovação: " + ", ".join(LABELS[a].lower() for a in asked) + "."
    return "allow", f"Permitido no modo {policy['mode']}."
