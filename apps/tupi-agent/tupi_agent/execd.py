"""tupi-agent-exec — executor das ferramentas da IA do TUPIOS.

Para cada chamada de ferramenta:
  1. PARAR IA ativo? limite de ações/minuto? -> nega
  2. Permission Manager classifica + decide + audita (allow / ask / deny)
  3. ask -> fila de aprovação (app/bolinha/CLI): uma vez | sempre nesta sessão | negar; timeout = negar
  4. executa numa unidade systemd TRANSITÓRIA, isolada, na slice tupi-agent.slice, com a menor
     identidade possível: "tupi-agent" (leitura), o próprio usuário (mudanças nos arquivos dele),
     root só em caminhos específicos já permitidos pela política (serviços, configuração do sistema)
  5. resultado (código de saída + saída resumida) vai para a auditoria; mudanças guardam "Desfazer"
"""
import grp
import json
import os
import pwd
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import uuid
from collections import deque

from . import policy as P
from . import st as ST

SOCKET_PATH = os.environ.get("TUPI_EXEC_SOCKET", "/run/tupi-agent/exec.sock")
PERM_SOCKET = os.environ.get("TUPI_PERM_SOCKET", "/run/tupi-perm-mgr/perm-mgr.sock")
STATE_DIR = os.environ.get("TUPI_AGENT_STATE", "/var/lib/tupi-agent")
HELPER = os.environ.get("TUPI_AGENT_HELPER", os.path.join(os.path.dirname(__file__), "helper.py"))
PYTHON = os.environ.get("TUPI_AGENT_PYTHON", "python3")
SYSTEM_PATH = os.environ.get("TUPI_AGENT_PATH", "/run/wrappers/bin:/run/current-system/sw/bin")
AGENT_USER = os.environ.get("TUPI_AGENT_USER", "tupi-agent")
GROUP = os.environ.get("TUPI_AGENT_GROUP", "tupi-ia")
SLICE = os.environ.get("TUPI_AGENT_SLICE", "tupi-agent.slice")
APPROVAL_TIMEOUT = int(os.environ.get("TUPI_AGENT_APPROVAL_TIMEOUT", "120"))
CMD_TIMEOUT = int(os.environ.get("TUPI_AGENT_COMMAND_TIMEOUT", "60"))
MAX_TIMEOUT = int(os.environ.get("TUPI_AGENT_MAX_TIMEOUT", "600"))
MAX_OUTPUT = int(os.environ.get("TUPI_AGENT_MAX_OUTPUT", "65536"))
RATE = int(os.environ.get("TUPI_AGENT_ACTIONS_PER_MINUTE", "20"))
FLAKE_REF = os.environ.get("TUPI_AGENT_FLAKE", "/etc/nixos#tupi-pd")

TOOLS = {"run_command", "read_file", "write_file", "list_dir", "system_info", "manage_service",
         "install_package", "open_app", "take_screenshot", "set_kernel_param",
         "st_run", "st_stage_change", "st_diff", "st_promote", "st_discard", "use_computer", "open_file"}
# Usar o computador (mouse/teclado): programas e limites
XDOTOOL = os.environ.get("TUPI_XDOTOOL", "xdotool")
MAGICK = os.environ.get("TUPI_MAGICK", "magick")
XDG_OPEN = os.environ.get("TUPI_XDG_OPEN", "xdg-open")
XPROP = os.environ.get("TUPI_XPROP", "xprop")      # ordem de empilhamento das janelas (trava do Pajé)
CONTROL_IDLE = int(os.environ.get("TUPI_AGENT_CONTROL_IDLE", "600"))   # "nesta conversa" expira parado
CONTROL_BUSY = 60          # outro agente usou o mouse há menos que isso: espera a vez
# Abrir apps/arquivos: xdg-open e gtk-launch abrem o programa e saem na hora. Sem ExitType=cgroup o systemd
# encerraria a unidade e mataria o programa recém-aberto junto (mesmo cgroup).
GUI_LAUNCH_PROPS = ["ExitType=cgroup"]
NOT_EXECUTABLE_SUFFIX = (".desktop", ".sh", ".bash", ".zsh", ".appimage", ".run", ".bin", ".py", ".pl", ".rb",
                         ".exe", ".msi", ".jar", ".deb", ".rpm", ".command")
ST_TOOLS = {"st_run", "st_stage_change", "st_diff", "st_promote", "st_discard"}
# Sempre só-leitura / inacessível dentro das unidades da IA (em qualquer modo)
ALWAYS_READONLY = ["-/etc/tupi", "-/var/log/tupi-audit"]
ALWAYS_HIDDEN = ["-/run/tupi-perm-mgr", "-/run/tupi-agent", "-/run/tupi-agents", "-/var/lib/tupi-agent"]
SECRET_HIDDEN = ["-{home}/.ssh", "-{home}/.gnupg", "-{home}/.password-store", "-{home}/.netrc",
                 "-{home}/.aws", "-/etc/tupi/ai-config.json"]
SYSTEM_WRITE_CATS = {"write_system_config", "delete_system_file", "modify_bootloader", "install_package",
                     "access_hardware"}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}          # id -> dict (com "event" e "answer")
        self.grants = {}           # (uid, sessão) -> set(categorias)
        self.control_grant = {}    # (uid, sessão) -> quando o usuário liberou o mouse/teclado nesta conversa
        self.control = {}          # uid -> {"session", "agente", "pos" (ponteiro real), "ultima"}: quem controla
        self.halted = False
        self.halted_by = None
        self.rate = {}             # uid -> deque(timestamps)
        self.actions = deque(maxlen=300)
        self.running = 0


S = State()


# ------------------------------------------------------------------ util
def perm(req, timeout=20):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(PERM_SOCKET)
        s.sendall(json.dumps(req).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8") or "{}")
    finally:
        s.close()


def event(**kw):
    try:
        perm(dict(kw, type="tool_event"))
    except OSError:
        pass


def short(text, n=300):
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


def peer(conn):
    return struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))


def allowed_user(uid):
    if uid == 0:
        return True
    try:
        user = pwd.getpwuid(uid)
        g = grp.getgrnam(GROUP)
        return user.pw_name in g.gr_mem or user.pw_gid == g.gr_gid
    except KeyError:
        return False


def owner_of(path, fallback_uid):
    """Dono do caminho (ou da pasta existente mais próxima)."""
    p = path
    while p and p != "/":
        try:
            return os.stat(p).st_uid
        except OSError:
            p = os.path.dirname(p)
    return fallback_uid


def unit_name():
    return "tupi-agent-tool-" + uuid.uuid4().hex[:10]


# ------------------------------------------------------ execução isolada
def systemd_run(cmd, *, uid, gid, home, cats, timeout, stdin=None, rw_paths=(), env=None, cwd=None,
                wait=True, network=None, extra_props=(), sandbox=True, memory="1G", bounding=None):
    """Roda cmd numa unidade transitória isolada. -> (exit_code, saída, truncado).

    sandbox=False é SÓ para os caminhos privilegiados específicos do executor (ensaio no ST, troca de
    geração do PD), depois de a política permitir: root, sem as proteções das ferramentas, mas ainda na
    slice da IA (limite de memória/tempo e o PARAR IA encerram)."""
    network = ("network_egress" in cats) if network is None else network
    privileged = "privilege_escalation" in cats
    system_write = bool(cats & SYSTEM_WRITE_CATS)
    secrets_ok = "read_secret" in cats
    if not sandbox:
        props = [f"RuntimeMaxSec={timeout}", f"MemoryMax={memory}"]
        network = True
    else:
        props = [
            f"RuntimeMaxSec={timeout}", f"MemoryMax={memory}", "TasksMax=256", "CPUQuota=200%",
            "PrivateTmp=yes", "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectKernelLogs=yes",
            "ProtectControlGroups=yes", "ProtectClock=yes", "ProtectHostname=yes", "RestrictRealtime=yes",
            "LockPersonality=yes", "RestrictSUIDSGID=" + ("no" if privileged else "yes"),
            "NoNewPrivileges=" + ("no" if privileged else "yes"),
            "ProtectSystem=" + ("no" if system_write else "strict"),
            # mesmo com sudo: sem remontar nada nem tirar atributos dos arquivos protegidos
            "CapabilityBoundingSet=" + (bounding or "~CAP_SYS_ADMIN CAP_LINUX_IMMUTABLE"),
            "ReadOnlyPaths=" + " ".join(ALWAYS_READONLY),
            "InaccessiblePaths=" + " ".join(ALWAYS_HIDDEN + ([] if secrets_ok else
                                                              [p.format(home=home) for p in SECRET_HIDDEN])),
        ]
    if not network:
        props.append("PrivateNetwork=yes")
    if rw_paths:
        props.append("ReadWritePaths=" + " ".join("-" + p for p in rw_paths))
    props += list(extra_props)
    # --expand-environment=no: sem isso o systemd troca "${...}" do comando pelo ambiente da unidade
    # (ex.: ${PIPESTATUS[0]} vira vazio) e o comando da IA/do executor roda diferente do que foi pedido
    args = ["systemd-run", "--quiet", "--collect", "--expand-environment=no", f"--unit={unit_name()}", f"--slice={SLICE}",
            f"--uid={uid}", f"--gid={gid}", f"--working-directory={cwd or home}",
            f"--setenv=HOME={home}", f"--setenv=PATH={SYSTEM_PATH}", "--setenv=LANG=pt_BR.UTF-8"]
    try:
        args.append(f"--setenv=USER={pwd.getpwuid(uid).pw_name}")
    except KeyError:
        pass
    for k, v in (env or {}).items():
        args.append(f"--setenv={k}={v}")
    for p in props:
        args += ["-p", p]
    if cmd and "/" not in cmd[0]:
        # o systemd-run procura o programa no PATH DELE (o do executor), não no da unidade: resolve aqui
        cmd = [shutil.which(cmd[0], path=SYSTEM_PATH) or cmd[0]] + list(cmd[1:])
    if not wait:
        r = subprocess.run(args + ["--no-block", "--"] + cmd, capture_output=True, timeout=30)
        return r.returncode, (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip(), False
    args += ["--wait", "--pipe", "--"] + cmd
    with S.lock:
        S.running += 1
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out = bytearray()
        truncated = False

        def feed():
            try:
                if stdin:
                    proc.stdin.write(stdin.encode("utf-8") if isinstance(stdin, str) else stdin)
                proc.stdin.close()
            except OSError:
                pass
        threading.Thread(target=feed, daemon=True).start()
        deadline = time.time() + timeout + 15
        while True:
            chunk = proc.stdout.read1(65536) if hasattr(proc.stdout, "read1") else proc.stdout.read(65536)
            if not chunk:
                break
            if len(out) < MAX_OUTPUT:
                out += chunk[:MAX_OUTPUT - len(out)]
            else:
                truncated = True
            if time.time() > deadline:
                proc.kill()
                break
        try:
            code = proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = -9
        text = out.decode("utf-8", "replace")
        if code == 143 or (code != 0 and "Unit was terminated" in text):
            pass
        return code, text, truncated
    finally:
        with S.lock:
            S.running -= 1


def privileged_script(script, timeout):
    """Caminho privilegiado específico (ST/promoção): bash como root na slice da IA. -> (código, saída)."""
    code, out, trunc = systemd_run(["bash", "-c", script], uid=0, gid=0, home="/root", cats=set(),
                                   timeout=timeout, sandbox=False, memory="6G", cwd="/")
    return code, out + ("\n[saída cortada]" if trunc else "")


def helper(subcmd, *args, uid, gid, home, cats, stdin=None, rw_paths=(), env=None, timeout=60,
           network=None, extra_props=(), bounding=None):
    code, out, trunc = systemd_run([PYTHON, HELPER, subcmd, *args], uid=uid, gid=gid, home=home, cats=cats,
                                   timeout=timeout, stdin=stdin, rw_paths=rw_paths, env=env, network=network,
                                   extra_props=extra_props, bounding=bounding)
    try:
        return code, json.loads(out), trunc
    except ValueError:
        return code, {"error": short(out, 2000) or f"falhou (código {code})"}, trunc


def agent_ids():
    p = pwd.getpwnam(AGENT_USER)
    return p.pw_uid, p.pw_gid


def expand(path, home):
    p = str(path or "").strip() or "."
    if p == "~" or p.startswith("~/"):
        p = home + p[1:]
    if not p.startswith("/"):
        p = os.path.join(home, p)
    return os.path.normpath(p)


# ------------------------------------------------------- desfazer (undo)
def undo_dir(action_id):
    d = os.path.join(STATE_DIR, "undo", action_id)
    os.makedirs(d, mode=0o711, exist_ok=True)
    os.chmod(os.path.join(STATE_DIR, "undo"), 0o711)
    return d


def backup_file(action_id, path, owner_uid):
    """Guarda o conteúdo atual do arquivo antes de a IA mexer (para o Desfazer)."""
    d = undo_dir(action_id)
    info = {"type": "file", "path": path, "existed": os.path.exists(path), "owner": owner_uid}
    if info["existed"] and os.path.isfile(path):
        dst = os.path.join(d, "backup")
        shutil.copy2(path, dst)
        os.chown(dst, owner_uid, -1)
        os.chmod(dst, 0o600)
        info["backup"] = dst
        info["mode"] = os.stat(path).st_mode & 0o7777
    with open(os.path.join(d, "undo.json"), "w") as f:
        json.dump(info, f)
    return info


# --------------------------------------------------------- ferramentas
def tool_list_dir(ctx, args):
    path = expand(args.get("path", "~"), ctx["home"])
    who = (ctx["uid"], ctx["gid"]) if "read_secret" in ctx["cats"] else agent_ids()
    code, res, _ = helper("list", path, uid=who[0], gid=who[1], home=ctx["home"], cats=ctx["cats"])
    return code, res


def tool_read_file(ctx, args):
    path = expand(args.get("path", ""), ctx["home"])
    max_bytes = str(min(int(args.get("max_bytes") or 20000), MAX_OUTPUT))
    who = (ctx["uid"], ctx["gid"]) if "read_secret" in ctx["cats"] else agent_ids()
    code, res, _ = helper("read", path, max_bytes, uid=who[0], gid=who[1], home=ctx["home"], cats=ctx["cats"])
    return code, res


def tool_system_info(ctx, args):
    uid, gid = agent_ids()
    code, res, _ = helper("info", uid=uid, gid=gid, home=ctx["home"], cats=ctx["cats"])
    if isinstance(res, dict):
        s = ST.status()
        res["st"] = {"disponivel": s["disponivel"], "motivo_indisponivel": s["motivo"] or None,
                     "estado": s["container"], "ensaio_pendente": s["ensaio"], "o_que_e": s["o_que_e"],
                     "limite": s["kernel"]}
        res["modo_live"] = ST.LIVE
    return code, res


def tool_write_file(ctx, args):
    path = expand(args.get("path", ""), ctx["home"])
    mode = "append" if args.get("mode") == "append" else "overwrite"
    content = str(args.get("content", ""))
    system_target = bool(ctx["cats"] & SYSTEM_WRITE_CATS)
    if system_target:
        uid, gid = 0, 0
    else:
        uid, gid = ctx["uid"], ctx["gid"]
    info = backup_file(ctx["action_id"], path, owner_of(path, uid))
    # /tmp das unidades é privado: para gravar no /tmp de verdade, desliga o PrivateTmp
    extra = ["PrivateTmp=no"] if path.startswith("/tmp/") else []
    code, res, _ = helper("write", path, mode, uid=uid, gid=gid, home=ctx["home"], cats=ctx["cats"],
                          stdin=content, rw_paths=[os.path.dirname(path)], extra_props=extra)
    if code == 0:
        ctx["undo"] = {"kind": "file", "info": info, "uid": uid, "gid": gid}
    return code, res


def tool_run_command(ctx, args):
    cmd = str(args.get("command", ""))
    timeout = max(1, min(int(args.get("timeout") or CMD_TIMEOUT), MAX_TIMEOUT))
    cwd = expand(args.get("cwd") or "~", ctx["home"])
    cats = ctx["cats"]
    if cats <= {"read"}:
        uid, gid = agent_ids()          # só leitura: identidade dedicada, sem acesso de escrita
        rw = []
    else:
        uid, gid = ctx["uid"], ctx["gid"]
        rw = [ctx["home"]] if "write_user_data" in cats or "execute_binary" in cats or "network_egress" in cats else []
    code, out, trunc = systemd_run(["bash", "-lc", cmd], uid=uid, gid=gid, home=ctx["home"], cats=cats,
                                   timeout=timeout, rw_paths=rw, cwd=cwd if os.path.isdir(cwd) else ctx["home"])
    if code == 143 or code == -9:
        out += f"\n[interrompido: passou do limite de {timeout}s ou foi parado]"
    return code, {"exit_code": code, "output": out, "truncated": trunc}


def tool_manage_service(ctx, args):
    name = str(args.get("name", ""))
    unit = name if "." in name else name + ".service"
    if not re.fullmatch(r"[A-Za-z0-9@._:-]+", unit):
        return 2, {"error": "nome de serviço inválido"}
    action = str(args.get("action", "status"))
    if action not in ("status", "start", "stop", "restart"):
        return 2, {"error": "ação inválida (status, start, stop, restart)"}
    if action == "status":
        r = subprocess.run(["systemctl", "status", "--no-pager", "--lines=15", unit], capture_output=True,
                           text=True, timeout=30)
        return 0, {"unit": unit, "active": subprocess.run(["systemctl", "is-active", unit], capture_output=True,
                                                          text=True).stdout.strip(), "status": short(r.stdout, 4000)}
    before = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
    r = subprocess.run(["systemctl", action, unit], capture_output=True, text=True, timeout=120)
    after = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
    if r.returncode == 0 and action in ("start", "stop"):
        ctx["undo"] = {"kind": "service", "unit": unit, "action": "stop" if action == "start" else "start",
                       "before": before}
    return r.returncode, {"unit": unit, "action": action, "before": before, "after": after,
                          "output": short(r.stdout + r.stderr, 2000)}


def tool_install_package(ctx, args):
    name = str(args.get("name", ""))
    if not P.PKG_NAME.match(name):
        return 2, {"error": "nome de pacote inválido"}
    if ST.LIVE or not os.path.isfile(os.path.join(ST.FLAKE_DIR, "flake.nix")):
        return 3, {"error": "Instalação persistente indisponível: este sistema não tem a configuração do TUPIOS "
                            f"em {ST.FLAKE_DIR} (modo live/ISO). Depois de instalar o TUPIOS no disco, a IA instala "
                            "programas pela configuração NixOS, ensaiando no ST e com ponto de retorno."}
    st_ok, _why = ST.available()
    if st_ok:
        # regra do TUPIOS: com o ST disponível, mudança de sistema é ensaiada antes
        return 3, {"error": "O ST está disponível: mudanças no sistema são ensaiadas antes. Use "
                            f"st_stage_change com pacotes=[\"{name}\"], mostre o resultado (st_diff) e, se o usuário "
                            "quiser, aplique com st_promote."}
    # sem ST (desativado): constrói antes de aplicar e guarda a geração anterior para o Desfazer
    with ST.LOCK:
        rels = [(os.path.relpath(ST.cfg_json(ST.FLAKE_DIR), ST.FLAKE_DIR), "alterado"),
                (os.path.relpath(ST.cfg_nix(ST.FLAKE_DIR), ST.FLAKE_DIR), "alterado")]
        saved = ST.backup_files(rels, undo_dir(ctx["action_id"]))
        cfg = ST.load_cfg(ST.FLAKE_DIR)
        if name in cfg["pacotes"]:
            return 0, {"ok": True, "message": f"{name} já está na configuração."}
        cfg["pacotes"].append(name)
        ST.write_cfg(ST.FLAKE_DIR, cfg)
        ST.git_track(rels[:1])
        code, out, top = ST.build(ST.PD_ATTR, ST.pd_link(), root=ST.FLAKE_DIR)
        if not top:
            ST.restore_files(saved)
            return code or 1, {"error": "A nova configuração não construiu; nada foi aplicado.",
                               "output": short(out, 3000)}
        code, out, old, snap = ST.apply_system(top, f"antes-instalar-{name}-{int(time.time())}")
        if code not in (0, 4):
            if code != 10:
                ST.switch_back(old)
            ST.restore_files(saved)
            return code, {"error": "A troca do sistema falhou; voltei à geração anterior.", "output": short(out, 3000)}
    ctx["undo"] = {"kind": "promote", "old_system": old, "new_system": top, "files": saved}
    return 0, {"ok": True, "rollback_point": {"generation": old, "btrfs_snapshot": snap}, "output": short(out, 3000)}


def tool_open_app(ctx, args):
    app = str(args.get("app", ""))
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", app):
        return 2, {"error": "nome de aplicativo inválido"}
    desktop = f"/run/current-system/sw/share/applications/{app}.desktop"
    cmd = ["gtk-launch", app] if os.path.exists(desktop) else [app]
    if not shutil.which(cmd[0], path=SYSTEM_PATH):
        return 2, {"error": f"aplicativo não encontrado: {app}"}
    env = desktop_env(ctx)         # XDG_DATA_DIRS: sem isso o gtk-launch não acha o atalho do app
    # Apps gráficos: precisam do socket do X (/tmp/.X11-unix) e de mais memória
    code, out, _ = systemd_run(cmd, uid=ctx["uid"], gid=ctx["gid"], home=ctx["home"], cats=ctx["cats"],
                               timeout=86400, env=env, wait=False, rw_paths=[ctx["home"]], network=True,
                               extra_props=["ProtectSystem=full", "PrivateTmp=no", "MemoryMax=6G", "TasksMax=4096",
                                            "CPUQuota=400%", *GUI_LAUNCH_PROPS])
    if code != 0:
        return code, {"error": f"não consegui abrir {app}: {short(out, 300)}"}
    return 0, {"ok": True, "opened": app}


def tool_take_screenshot(ctx, args):
    env = {k: v for k, v in (("DISPLAY", ctx.get("display")), ("XAUTHORITY", ctx.get("xauthority"))) if v}
    if "DISPLAY" not in env:
        return 2, {"error": "sem tela gráfica para capturar"}
    out_dir = os.path.join(ctx["home"], "Imagens", "Capturas TUPIOS")
    code, res, _ = helper("screenshot", out_dir, uid=ctx["uid"], gid=ctx["gid"], home=ctx["home"], cats=ctx["cats"],
                          env=env, rw_paths=[ctx["home"]], network=True, extra_props=["PrivateTmp=no"])
    return code, res


# ------------------------------------------- usar o computador (assistido)
def desktop_env(ctx):
    """Ambiente da sessão gráfica para programas do usuário (xdotool, xdg-open, apps). No NixOS os atalhos
    .desktop e os tipos de arquivo ficam nos perfis: sem XDG_DATA_DIRS o xdg-open/gtk-launch não acham nada."""
    env = {k: v for k, v in (("DISPLAY", ctx.get("display")), ("XAUTHORITY", ctx.get("xauthority"))) if v}
    if env:
        run = f"/run/user/{ctx['uid']}"
        user = ctx.get("user") or ""
        env.update(TUPI_XDOTOOL=XDOTOOL, TUPI_MAGICK=MAGICK, TUPI_XPROP=XPROP, XDG_RUNTIME_DIR=run,
                   DBUS_SESSION_BUS_ADDRESS=f"unix:path={run}/bus", XDG_CONFIG_DIRS="/etc/xdg",
                   XDG_DATA_DIRS=":".join([f"{ctx['home']}/.nix-profile/share", f"/etc/profiles/per-user/{user}/share",
                                           "/run/current-system/sw/share"]))
        if ctx.get("desktop"):
            env["XDG_CURRENT_DESKTOP"] = ctx["desktop"]
    return env


def control_guard(ctx):
    """Antes de mexer no mouse/teclado: ninguém esperando aprovação (a IA nunca clica no próprio "Permitir")
    e só um agente por vez na tela. -> mensagem de recusa ou None."""
    with S.lock:
        waiting = [p for p in S.pending.values() if p["uid"] == ctx["uid"]]
        ctl = S.control.get(ctx["uid"])
    if waiting:
        return ("Há um pedido de aprovação esperando você. Enquanto isso a IA não mexe no mouse nem no teclado "
                "(assim ela nunca aprova a si mesma). Responda o pedido e peça para continuar.")
    # outro agente (ou outro trabalho do mesmo subagente) usou a tela há pouco: espera a vez. O Pajé trocar de
    # conversa não conta (é você conversando com ele).
    other = ctl and (ctl.get("agente") != ctx.get("agente") or
                     (ctx.get("agente") and ctl.get("session") != ctx.get("session")))
    if other and time.time() - ctl["ultima"] < CONTROL_BUSY:
        return (f"{ctl.get('agente') or 'O Pajé'} está usando o mouse e o teclado agora. Só um agente por vez "
                "controla a tela: espere ele terminar e tente de novo.")
    return None


def revoke_control(uid, why):
    """O controle volta para o usuário: cancela o "Permitir nesta conversa" do mouse/teclado (todas as conversas)."""
    with S.lock:
        had = uid in S.control or any(k[0] == uid for k in S.control_grant)
        for key, cats in S.grants.items():
            if key[0] == uid:
                cats.discard("control_desktop")
        for key in [k for k in S.control_grant if k[0] == uid]:
            S.control_grant.pop(key, None)
        S.control.pop(uid, None)
    if had:
        try:
            event(event="controle_devolvido", user=pwd.getpwuid(uid).pw_name, motivo=why)
        except KeyError:
            pass
    return had


def control_grant_valid(uid, session):
    with S.lock:
        t0 = S.control_grant.get((uid, session))
        last = (S.control.get(uid) or {}).get("ultima", 0)
    return bool(t0) and time.time() - max(t0, last) < CONTROL_IDLE


def tool_use_computer(ctx, args):
    env = desktop_env(ctx)
    if "DISPLAY" not in env:
        return 2, {"error": "sem tela gráfica: não há o que ver nem onde clicar"}
    action = str(args.get("action", ""))
    acts = action not in P.COMPUTER_READ
    if acts:
        why = control_guard(ctx)
        if why:
            return 4, {"error": why}
    with S.lock:
        ctl = S.control.get(ctx["uid"])
    req = {k: args.get(k) for k in ("action", "x", "y", "x2", "y2", "text", "keys", "button", "direction", "amount",
                                    "seconds") if args.get(k) is not None}
    # liberado "nesta conversa": se o ponteiro não está onde a IA o deixou, foi você que mexeu -> o controle volta
    if acts and ctx.get("approved_by") == "sessão" and ctl and ctl.get("pos"):
        req["esperado"] = ctl["pos"]
    code, res, _ = helper("desktop", uid=ctx["uid"], gid=ctx["gid"], home=ctx["home"], cats=ctx["cats"],
                          stdin=json.dumps(req), env=env, rw_paths=[ctx["home"]], network=True,
                          extra_props=["PrivateTmp=no"], timeout=90)
    if isinstance(res, dict) and res.get("assumiu"):
        revoke_control(ctx["uid"], "o usuário mexeu no mouse")
        return 5, {"error": "Você mexeu no mouse: o controle voltou para você e a permissão de usar o mouse e o "
                            "teclado nesta conversa foi cancelada. Se quiser que a IA continue, peça de novo "
                            "(ela vai pedir aprovação).", "controle_devolvido": True}
    if code == 0 and acts:
        with S.lock:
            S.control[ctx["uid"]] = {"session": ctx.get("session"), "agente": ctx.get("agente"),
                                     "pos": res.get("pos_real"), "ultima": time.time()}
    if isinstance(res, dict):
        res.pop("pos_real", None)
    return code, res


def open_file_problem(args, home):
    """Antes de perguntar: o arquivo existe e não é programa/script? -> motivo da recusa ou None."""
    path = expand(args.get("path", ""), home)
    if not os.path.exists(path):
        return f"não existe: {path}"
    if os.path.isfile(path) and (os.stat(path).st_mode & 0o111 or path.lower().endswith(NOT_EXECUTABLE_SUFFIX)):
        return ("Por segurança open_file não abre programas nem scripts (isso os executaria). "
                "Para aplicativos use open_app; para ver o conteúdo, read_file.")
    return None


def tool_open_file(ctx, args):
    env = desktop_env(ctx)
    if "DISPLAY" not in env:
        return 2, {"error": "sem tela gráfica para abrir o arquivo"}
    path = expand(args.get("path", ""), ctx["home"])
    bad = open_file_problem(args, ctx["home"])
    if bad:
        return 2, {"error": bad}
    why = control_guard(ctx)
    if why:
        return 4, {"error": why}
    code, out, _ = systemd_run([XDG_OPEN, path], uid=ctx["uid"], gid=ctx["gid"], home=ctx["home"], cats=ctx["cats"],
                               timeout=86400, env=env, wait=False, rw_paths=[ctx["home"]], network=True,
                               extra_props=["ProtectSystem=full", "PrivateTmp=no", "MemoryMax=6G", "TasksMax=4096",
                                            "CPUQuota=400%", *GUI_LAUNCH_PROPS])
    if code != 0:
        return code, {"error": f"não consegui abrir {path}: {short(out, 300)}"}
    with S.lock:
        prev = S.control.get(ctx["uid"]) or {}
        S.control[ctx["uid"]] = {"session": ctx.get("session"), "agente": ctx.get("agente"),
                                 "pos": prev.get("pos") if prev.get("session") == ctx.get("session") else None,
                                 "ultima": time.time()}
    return 0, {"ok": True, "aberto": path,
               "dica": "o programa leva alguns segundos para abrir: use use_computer screenshot para ver a tela"}


def op_revoke_control(uid):
    had = revoke_control(uid, "botão Devolver o controle")
    return {"ok": True, "tinha_controle": had}


def sysctl_path(name):
    return "/proc/sys/" + name.replace(".", "/")


def write_sysctl(path, value, cats):
    """Grava UM parâmetro do kernel. A unidade continua isolada; só aquele arquivo de /proc/sys fica gravável
    (ProtectKernelTunables desligado SÓ aqui, com /proc/sys e /sys somente leitura e uma exceção para o caminho).
    Sem PrivateNetwork: net.* é por namespace de rede e precisa valer no sistema."""
    code, res, _ = helper("sysctl", path, value, uid=0, gid=0, home="/root", cats=set(cats), network=True,
                          rw_paths=[path], extra_props=["ProtectKernelTunables=no", "ReadOnlyPaths=/proc/sys /sys"],
                          bounding="CAP_SYS_ADMIN CAP_NET_ADMIN CAP_SYS_RESOURCE CAP_DAC_OVERRIDE")
    return code, res


def read_sysctl(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def tool_set_kernel_param(ctx, args):
    name, value = str(args.get("name", "")).strip(), str(args.get("value", "")).strip()
    if not P.SYSCTL_NAME.match(name) or ".." in name or not P.SYSCTL_VALUE.match(value):
        return 2, {"error": "nome ou valor de parâmetro inválido", "onde": "PD"}
    path = sysctl_path(name)
    if not os.path.isfile(path):
        return 2, {"error": f"o parâmetro {name} não existe neste kernel", "onde": "PD"}
    old = read_sysctl(path)
    if old is None:
        return 2, {"error": f"não consegui ler {name}", "onde": "PD"}
    st_ok, _ = ST.available()
    permanent = (f"Isto some ao reiniciar. Para manter: st_stage_change com sysctl {{\"{name}\": \"{value}\"}} e "
                 "depois st_promote (o efeito do kernel não é ensaiado no ST, só a configuração)." if st_ok else
                 "Isto some ao reiniciar. Para manter é preciso o TUPIOS instalado no disco (configuração NixOS + ST)."
                 if ST.LIVE or not st_ok else "")
    if " ".join(old.split()) == " ".join(value.split()):
        return 0, {"ok": True, "parametro": name, "valor": old, "mensagem": "já estava com esse valor; nada mudou",
                   "onde": "PD"}
    code, res = write_sysctl(path, value, ctx["cats"])
    if not isinstance(res, dict):
        res = {"output": str(res)}
    res.update(onde="PD", temporario=True, permanente=permanent)
    if code == 0 and name not in P.SYSCTL_NO_UNDO:
        ctx["undo"] = {"kind": "sysctl", "name": name, "path": path, "old": old}
    return code, res


def tool_st_run(ctx, args):
    cmd = str(args.get("command", ""))
    timeout = max(1, min(int(args.get("timeout") or CMD_TIMEOUT), MAX_TIMEOUT))
    if P.touches_kernel(cmd, ctx["home"]):
        return 3, {"error": "Isto NÃO pode ser ensaiado no ST. " + ST.KERNEL_NOTE + " Para tornar permanente: "
                            "st_stage_change com sysctl (valida a configuração) e depois st_promote.",
                   "kernel_ensaiado": False, "onde": "ST"}
    ok, msg = ST.ensure_container()
    if not ok:
        return 1, {"error": msg, "onde": "ST"}
    code, out = ST.container_run(cmd, timeout)
    return code, {"exit_code": code, "output": out, "onde": "ST"}


def tool_st_stage_change(ctx, args):
    change = {k: args.get(k) for k in ("pacotes", "remover_pacotes", "sysctl", "parametros_kernel", "nix",
                                       "servico_alvo") if args.get(k)}
    return ST.stage(change, str(args.get("descricao") or "").strip())


def tool_st_diff(ctx, args):
    return ST.current_diff()


def tool_st_promote(ctx, args):
    code, res, undo = ST.promote(undo_dir(ctx["action_id"]))
    if undo:
        ctx["undo"] = undo
    return code, res


def tool_st_discard(ctx, args):
    return ST.discard()


RUNNERS = {"list_dir": tool_list_dir, "read_file": tool_read_file, "system_info": tool_system_info,
           "write_file": tool_write_file, "run_command": tool_run_command, "manage_service": tool_manage_service,
           "install_package": tool_install_package, "open_app": tool_open_app,
           "take_screenshot": tool_take_screenshot, "set_kernel_param": tool_set_kernel_param,
           "st_run": tool_st_run, "st_stage_change": tool_st_stage_change, "st_diff": tool_st_diff,
           "st_promote": tool_st_promote, "st_discard": tool_st_discard,
           "use_computer": tool_use_computer, "open_file": tool_open_file}

# operações privilegiadas do ST/promoção: unidade root na slice da IA (ver privileged_script)
ST.RUN = privileged_script


def approval_details(tool, args):
    """Texto extra mostrado no pedido de aprovação (o usuário decide vendo o que muda)."""
    if tool == "st_promote":
        stg = ST.load_stage() or {}
        checks = stg.get("verificacoes") or {}
        head = [f"Ensaio {stg.get('id')}: {stg.get('descricao') or ''}",
                "Verificações no ST: " + ("OK" if checks.get("ok") else "COM PROBLEMAS") +
                (f" — unidades com falha novas: {', '.join(checks.get('unidades_com_falha_novas') or [])}"
                 if checks.get("unidades_com_falha_novas") else "")]
        if checks.get("kernel"):
            head.append(checks["kernel"])
        return "\n".join(head) + "\n\n" + ST.diff_text(stg.get("diff") or {}, 5000)
    if tool == "set_kernel_param":
        name = str(args.get("name", ""))
        old = read_sysctl(sysctl_path(name)) if P.SYSCTL_NAME.match(name) and ".." not in name else None
        return (f"{name}: valor atual {old!r} -> novo {str(args.get('value'))!r}\n"
                "Vale agora, no PD, até reiniciar. Desfazer volta o valor atual.")
    if tool in ("use_computer", "open_file"):
        return ("Com o mouse e o teclado a IA faz o que você faria nesta tela, em qualquer programa aberto. "
                "Acompanhe: para retomar o controle, mexa o mouse ou clique em \"Devolver o controle\" na faixa "
                "do topo da tela (PARAR IA também corta). Ela não clica nas janelas do Pajé e não age enquanto "
                "houver um pedido de aprovação esperando você.\n"
                "\"Permitir nesta conversa\" vale até você mexer no mouse ou ficar "
                f"{CONTROL_IDLE // 60} minutos sem uso.")
    return None


# ------------------------------------------------------------ operações
def op_run(uid, req):
    user = pwd.getpwuid(uid)
    tool, args, session = req.get("tool"), req.get("args") or {}, str(req.get("session") or "")
    # quem pediu: o Pajé (vazio) ou um subagente dele (nome vem do motor do Pajé; só rotula, não dá permissão)
    agente = re.sub(r"[^\wÀ-ÿ .'-]", "", str(req.get("agente") or ""))[:40].strip() or None
    if tool not in TOOLS or not isinstance(args, dict):
        return {"status": "denied", "reason": f"ferramenta desconhecida: {tool}"}
    if S.halted:
        return {"status": "denied", "reason": "A IA está PARADA (PARAR IA). Clique em Retomar para continuar."}
    now = time.time()
    with S.lock:
        q = S.rate.setdefault(uid, deque())
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE:
            return {"status": "denied", "reason": f"Limite de {RATE} ações por minuto atingido. Aguarde."}
        q.append(now)
    st_ok, st_why = ST.available() if tool in ST_TOOLS else (True, "")
    if tool == "st_promote":
        # a política decide sobre a mudança REALMENTE ensaiada, não sobre o que a IA diz
        stg = (ST.load_stage() if st_ok else None) or {}
        args = {"mudanca": stg.get("mudanca_total") or {}, "descricao": stg.get("descricao") or ""}
    d = perm({"type": "tool_decide", "uid": uid, "user": user.pw_name, "home": user.pw_dir, "tool": tool,
              "args": args, "session": session, "agente": agente})
    d["agente"] = agente
    rid = d.get("request_id") or uuid.uuid4().hex[:12]
    base = {"request_id": rid, "decision": d.get("decision"), "categories": d.get("categories", []),
            "risk": d.get("risk"), "summary": d.get("summary"), "reason": d.get("reason"),
            "destructive": d.get("destructive", False), "onde": d.get("where") or "PD", "agente": agente}
    if d.get("decision") == "deny" or d.get("decision") not in ("allow", "ask"):
        return dict(base, status="denied")
    bad = open_file_problem(args, user.pw_dir) if tool == "open_file" else None
    if bad:      # recusa sem pedir aprovação à toa
        event(event="resultado", request_id=rid, user=user.pw_name, tool=tool, agente=agente, exit_code=2,
              duration=0, output=short(bad, 300), approved_by="-")
        return dict(base, status="error", exit_code=2, result={"error": bad}, reason=bad, approved_by=None,
                    can_undo=False)
    # ST indisponível (ex.: ISO live), nada em ensaio ou kernel no ST: responde claro, sem pedir aprovação à toa
    kernel_in_st = tool == "st_run" and P.touches_kernel(str(args.get("command", "")), user.pw_dir)
    if not st_ok or (tool == "st_promote" and not args["mudanca"]) or kernel_in_st:
        why = (st_why if not st_ok else
               ("Isto NÃO pode ser ensaiado no ST. " + ST.KERNEL_NOTE + " Para tornar permanente: st_stage_change "
                "com sysctl (valida a configuração) e depois st_promote.") if kernel_in_st else
               "não há mudança em ensaio no ST: use st_stage_change antes")
        event(event="resultado", request_id=rid, user=user.pw_name, tool=tool, exit_code=3, duration=0,
              output=short(why, 300), approved_by="-")
        return dict(base, status="error", exit_code=3, result={"error": why, "st_disponivel": st_ok},
                    reason=why, approved_by=None, can_undo=False)
    approved_by = "política"
    if d["decision"] == "ask":
        cats = set(d.get("categories", []))
        with S.lock:
            granted = set(S.grants.get((uid, session), set()))
        auto = bool(session) and not d.get("destructive") and cats <= granted and not cats & P.ALWAYS_ASK
        if auto and "control_desktop" in cats and not control_grant_valid(uid, session):
            revoke_control(uid, f"{CONTROL_IDLE // 60} minutos sem usar")   # liberação parada demais: pergunta de novo
            auto = False
        if auto:
            approved_by = "sessão"
            event(event="aprovacao", request_id=rid, user=user.pw_name, outcome="aprovado", by="sessão")
        else:
            answer = wait_approval(uid, user.pw_name, session, d, args, approval_details(tool, args))
            event(event="aprovacao", request_id=rid, user=user.pw_name, outcome=answer, by="usuário")
            if answer not in ("once", "session"):
                reason = {"timeout": f"Sem resposta em {APPROVAL_TIMEOUT}s: negado automaticamente.",
                          "cancelled": "Cancelado pelo PARAR IA."}.get(answer, "Você negou esta ação.")
                return dict(base, status="denied", reason=reason, approval=answer)
            if answer == "session" and not d.get("destructive"):
                with S.lock:
                    # "sempre nesta sessão" nunca cobre o que pergunta a cada vez (aplicar no PD)
                    S.grants.setdefault((uid, session), set()).update(cats - P.ALWAYS_ASK)
                    if "control_desktop" in cats:
                        S.control_grant[(uid, session)] = time.time()
            approved_by = "usuário"
    if S.halted:
        return dict(base, status="denied", reason="A IA foi PARADA antes de executar.")
    action_id = uuid.uuid4().hex[:12]
    ctx = {"uid": uid, "gid": user.pw_gid, "home": user.pw_dir, "user": user.pw_name, "action_id": action_id,
           "cats": set(d.get("categories", [])), "display": req.get("display"), "xauthority": req.get("xauthority"),
           "session": session, "agente": agente, "approved_by": approved_by,
           "desktop": re.sub(r"[^A-Za-z:]", "", str(req.get("desktop") or ""))[:40]}
    t0 = time.time()
    try:
        code, res = RUNNERS[tool](ctx, args)
    except Exception as e:
        code, res = 1, {"error": f"falha no executor: {e}"}
    duration = round(time.time() - t0, 2)
    status = "ok" if code == 0 else "error"
    if S.halted:
        # PARAR IA durante a execução: o systemd encerra a unidade com SIGTERM, o que conta como
        # saída limpa (código 0) — mas a ação foi interrompida, não concluída.
        status = "error"
        base["reason"] = "Interrompida pelo PARAR IA."
        res = dict(res, interrompida=True) if isinstance(res, dict) else {"output": str(res), "interrompida": True}
    out_text = res.get("output") if isinstance(res, dict) and "output" in res else json.dumps(res, ensure_ascii=False)
    event(event="resultado", request_id=rid, user=user.pw_name, tool=tool, agente=agente, exit_code=code, duration=duration,
          output=short(out_text, 300), approved_by=approved_by)
    undo = ctx.get("undo")
    with S.lock:
        S.actions.append({"action_id": action_id, "uid": uid, "tool": tool, "summary": d.get("summary"),
                          "time": time.time(), "status": status, "undo": undo, "undone": False,
                          "onde": base["onde"]})
    return dict(base, status=status, exit_code=code, result=res, duration=duration, action_id=action_id,
                approved_by=approved_by, can_undo=bool(undo))


def wait_approval(uid, username, session, d, args, detalhes=None):
    shown = dict(args)
    if "content" in shown:
        shown["content"] = short(str(shown["content"]), 1500)
    item = {"id": d["request_id"], "uid": uid, "user": username, "session": session, "tool": d.get("tool"),
            "summary": d.get("summary"), "target": d.get("target"), "args": shown,
            "categories": d.get("categories", []), "labels": d.get("labels", []), "risk": d.get("risk"),
            "destructive": d.get("destructive", False), "reason": d.get("reason"), "reasons": d.get("reasons", []),
            "onde": d.get("where") or "PD", "detalhes": detalhes, "agente": d.get("agente"),
            "created": time.time(), "expires": time.time() + APPROVAL_TIMEOUT,
            "event": threading.Event(), "answer": None}
    with S.lock:
        S.pending[item["id"]] = item
    item["event"].wait(APPROVAL_TIMEOUT)
    with S.lock:
        S.pending.pop(item["id"], None)
    return item["answer"] or "timeout"


def public_pending(item):
    return {k: v for k, v in item.items() if k not in ("event", "answer")}


def op_decide(uid, req):
    with S.lock:
        item = S.pending.get(req.get("id"))
    if not item or (uid != 0 and item["uid"] != uid):
        return {"ok": False, "error": "pedido não encontrado (já respondido ou expirou)"}
    decision = req.get("decision")
    if decision not in ("once", "session", "deny"):
        return {"ok": False, "error": "decisão inválida (once, session, deny)"}
    if decision != "deny" and item["destructive"] and not req.get("confirm_destructive"):
        return {"ok": False, "error": "Comando destrutivo: confirme de novo (confirm_destructive)."}
    if decision == "session" and item["destructive"]:
        decision = "once"   # destrutivo nunca vira "sempre"
    item["answer"] = decision
    item["event"].set()
    return {"ok": True, "decision": decision}


def op_stop(uid):
    with S.lock:
        S.halted, S.halted_by = True, uid
        items = list(S.pending.values())
        # PARAR IA também devolve o mouse/teclado: depois de Retomar, usar a tela pede aprovação de novo
        S.control.clear()
        S.control_grant.clear()
        for cats in S.grants.values():
            cats.discard("control_desktop")
    for item in items:
        item["answer"] = "cancelled"
        item["event"].set()
    r = subprocess.run(["systemctl", "stop", SLICE], capture_output=True, text=True, timeout=60)
    st_stopped = False
    if ST.ST_ENABLED and ST.container_state() != "parado":   # o ST também para (o ensaio fica guardado)
        st_stopped = subprocess.run(["systemctl", "stop", f"container@{ST.CONTAINER}.service"],
                                    capture_output=True, timeout=120).returncode == 0
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    event(event="parar_ia", user=user, cancelled_pending=len(items), slice_stopped=r.returncode == 0,
          st_stopped=st_stopped)
    return {"ok": True, "cancelled": len(items), "halted": True}


def op_resume(uid):
    with S.lock:
        S.halted = False
    event(event="retomar_ia", user=pwd.getpwuid(uid).pw_name)
    return {"ok": True, "halted": False}


def op_undo(uid, req):
    with S.lock:
        act = next((a for a in S.actions if a["action_id"] == req.get("action_id")), None)
    if not act or (uid != 0 and act["uid"] != uid):
        return {"ok": False, "error": "ação não encontrada"}
    if act["undone"] or not act["undo"]:
        return {"ok": False, "error": "esta ação não pode ser desfeita (ou já foi)"}
    u = act["undo"]
    user = pwd.getpwuid(act["uid"])
    if u["kind"] == "file":
        info = u["info"]
        target = info["path"]
        cats = {"write_system_config"} if u["uid"] == 0 else {"write_user_data"}
        if info.get("backup"):
            # O executor (root) lê a cópia; a unidade isolada só recebe o conteúdo
            with open(info["backup"], "rb") as f:
                data = f.read()
            code, res, _ = helper("write", target, "overwrite", uid=u["uid"], gid=u["gid"], home=user.pw_dir,
                                  cats=cats, stdin=data, rw_paths=[os.path.dirname(target)])
        elif not info["existed"]:
            code, res, _ = helper("remove", target, uid=u["uid"], gid=u["gid"], home=user.pw_dir, cats=cats,
                                  rw_paths=[os.path.dirname(target)])
        else:
            return {"ok": False, "error": "sem cópia de segurança"}
        ok = code == 0
    elif u["kind"] == "service":
        r = subprocess.run(["systemctl", u["action"], u["unit"]], capture_output=True, text=True, timeout=120)
        ok, res = r.returncode == 0, {"output": short(r.stdout + r.stderr)}
    elif u["kind"] == "sysctl":
        code, res = write_sysctl(u["path"], u["old"], {"tune_kernel"})
        ok = code == 0
    elif u["kind"] in ("promote", "generation"):
        if u["kind"] == "generation":             # registro antigo: só a geração
            u = {"old_system": u.get("generation"), "files": []}
        ok, res = ST.undo_promote(u)
    else:
        return {"ok": False, "error": "tipo de desfazer desconhecido"}
    if ok:
        act["undone"] = True
    event(event="desfazer", user=user.pw_name, action_id=act["action_id"], ok=ok)
    return {"ok": ok, "result": res}


def op_status(uid):
    with S.lock:
        pend = [public_pending(p) for p in S.pending.values() if uid == 0 or p["uid"] == uid]
        running = S.running
    try:
        pol = perm({"type": "policy_show"})
        policy = {"mode": pol.get("mode"), "livre": pol.get("livre")}
    except OSError:
        policy = {"mode": None}
    st_ok, _ = ST.available()
    stg = ST.brief(ST.load_stage()) if st_ok else None
    with S.lock:
        ctl = S.control.get(uid)
        liberado = any(k[0] == uid for k in S.control_grant)
    controle = None
    if ctl or liberado:
        last = (ctl or {}).get("ultima", 0)
        controle = {"agente": (ctl or {}).get("agente") or "Pajé", "ultima": last, "liberado": liberado,
                    "ativo": time.time() - last < 20, "sessao": (ctl or {}).get("session")}
    return {"halted": S.halted, "pending": pend, "running": running, "policy": policy, "controle": controle,
            "st": {"disponivel": st_ok, "ensaio": stg},
            "limits": {"approval_timeout": APPROVAL_TIMEOUT, "command_timeout": CMD_TIMEOUT,
                       "max_output": MAX_OUTPUT, "actions_per_minute": RATE}}


def op_st_status(uid):
    """Painel 'Mudanças em ensaio' / tupi-agent st: estado do ST e o diff do ensaio atual."""
    s = ST.status()
    if s["disponivel"] and s["ensaio"]:
        stg = ST.load_stage() or {}
        s["verificacoes"] = stg.get("verificacoes")
        s["mudancas"] = [{"descricao": m.get("descricao"), "resumo": P.change_summary(m.get("mudanca"))}
                         for m in stg.get("mudancas") or []]
        s["diff"] = stg.get("diff")
        s["diff_texto"] = ST.diff_text(stg.get("diff") or {}, 20000)
    return s


LOG_EVENTS = {"agente_criado", "agente_editado", "agente_removido", "trabalho_criado", "trabalho_concluido",
              "trabalhos_cancelados", "alma_do_paje_editada"}


def op_log(uid, req):
    """Eventos de orquestração do Pajé (motor sem privilégio) -> log de auditoria, com o usuário real (SO_PEERCRED)."""
    ev = str(req.get("evento") or "")
    if ev not in LOG_EVENTS:
        return {"ok": False, "error": "evento não permitido"}
    fields = {str(k)[:30]: short(str(v), 300) for k, v in req.items() if k not in ("op", "evento", "event", "user")}
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    event(event=ev, user=user, **dict(list(fields.items())[:10]))
    return {"ok": True}


def op_st_discard(uid):
    """Descartar pelo app/CLI (ação do próprio usuário: não precisa de aprovação, mas fica na auditoria)."""
    code, res = ST.discard()
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    event(event="st_descartado", user=user, ok=code == 0, ensaio=(res.get("descartado") or {}).get("id"))
    return dict(res, ok=code == 0)


def op_history(uid):
    with S.lock:
        acts = [dict({k: v for k, v in a.items() if k != "undo"}, can_undo=bool(a["undo"]) and not a["undone"])
                for a in S.actions if uid == 0 or a["uid"] == uid]
    return {"actions": acts[-50:]}


def handle(conn):
    try:
        pid, uid, _gid = peer(conn)
        conn.settimeout(15)
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 8 * 1024 * 1024:
                break
        conn.settimeout(None)
        req = json.loads(buf.decode("utf-8") or "{}")
        if not allowed_user(uid):
            resp = {"status": "denied", "error": f"usuário sem permissão (precisa estar no grupo {GROUP})"}
        else:
            op = req.get("op")
            resp = {"run": lambda: op_run(uid, req), "decide": lambda: op_decide(uid, req),
                    "pending": lambda: {"pending": op_status(uid)["pending"]}, "stop": lambda: op_stop(uid),
                    "resume": lambda: op_resume(uid), "status": lambda: op_status(uid),
                    "undo": lambda: op_undo(uid, req), "history": lambda: op_history(uid),
                    "st_status": lambda: op_st_status(uid), "st_discard": lambda: op_st_discard(uid),
                    "log": lambda: op_log(uid, req),
                    "revoke_control": lambda: op_revoke_control(uid)}.get(
                op, lambda: {"error": f"operação desconhecida: {op}"})()
    except Exception as e:
        resp = {"status": "error", "error": f"executor: {e}"}
    try:
        conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
    except OSError:
        pass
    finally:
        conn.close()


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCKET_PATH)
    try:
        os.chown(SOCKET_PATH, 0, grp.getgrnam(GROUP).gr_gid)
    except KeyError:
        pass
    os.chmod(SOCKET_PATH, 0o660)
    srv.listen(32)
    print(f"[AgentExec] ouvindo em {SOCKET_PATH} | aprovação expira em {APPROVAL_TIMEOUT}s | "
          f"{RATE} ações/min", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=handle, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
