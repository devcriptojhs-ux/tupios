"""tupi-perm-mgr — Permission Manager do TUPIOS.

Socket Unix (root, grupo tupi-ia). Pedidos JSON:
  - legado {"agent_id","operation","target"}           -> decisão por operação (orquestrador, tupi-perm-check)
  - {"type":"tool_decide", ...}   (só o executor/root)  -> classifica + decide + audita uma chamada de ferramenta
  - {"type":"tool_check", ...}                          -> simulação (não executa, não audita)
  - {"type":"tool_event", ...}    (só root)             -> audita aprovação/negação/resultado/PARAR
  - {"type":"policy_show"}                              -> política efetiva (modo, tabela, origens)
  - {"type":"policy_set" | "policy_reset", ...}         -> altera /etc/tupi/agent-policy.json (pedido HUMANO;
                                                           negado a processos da IA)
Todo evento vai para o log de auditoria encadeado por hash.
"""
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import time
import uuid

from . import policy as P

LEGACY_POLICY = os.environ.get("TUPI_POLICY", "/etc/tupi/permission-policy.json")
AGENT_BASE = os.environ.get("TUPI_AGENT_BASE_POLICY", "/etc/tupi/agent-base-policy.json")
AGENT_RUNTIME = os.environ.get("TUPI_AGENT_POLICY", "/etc/tupi/agent-policy.json")
AUDIT_LOG = os.environ.get("TUPI_AUDIT_LOG", "/var/log/tupi-audit/permission.log")
AUTO_APPROVE_RO = os.environ.get("TUPI_AUTO_APPROVE_READONLY", "1") == "1"
MAX_CONCURRENT = int(os.environ.get("TUPI_MAX_CONCURRENT", "10"))
SOCKET_PATH = os.environ.get("TUPI_SOCKET", "/run/tupi-perm-mgr/perm-mgr.sock")
AI_SLICE = os.environ.get("TUPI_AGENT_SLICE", "tupi-agent.slice")
GROUP = "tupi-ia"
LIVRE_CONFIRM = "ATIVAR MODO LIVRE"
CRITICAL_CONFIRM = "LIBERAR CATEGORIAS CRÍTICAS"

audit_lock = threading.Lock()
policy_lock = threading.Lock()


# ------------------------------------------------------------------ util
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        return json.loads(text) if text.strip() else default
    except (OSError, ValueError):
        return default


def boot_id():
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip()
    except OSError:
        return None


def current_policy():
    return P.effective_policy(load_json(AGENT_BASE, {}), load_json(AGENT_RUNTIME, {}), boot_id=boot_id())


def audit(entry):
    """Acrescenta ao log de auditoria (append-only) com hash encadeado."""
    entry = dict(entry)
    entry.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    entry["hash"] = ""
    with audit_lock:
        prev = "genesis"
        try:
            with open(AUDIT_LOG, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                lines = f.read().decode("utf-8", "replace").splitlines()
            if lines:
                prev = json.loads(lines[-1]).get("hash", "genesis")
        except (OSError, ValueError):
            pass
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        entry["hash"] = hashlib.sha256(f"{prev}:{line}".encode()).hexdigest()[:16]
        try:
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as e:
            print(f"[PermMgr] falha ao gravar auditoria: {e}", file=sys.stderr, flush=True)


def peer(conn):
    pid, uid, gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return pid, uid, gid


def in_ai_slice(pid):
    """O processo que pediu está rodando dentro de uma unidade de ferramenta da IA?"""
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            return AI_SLICE in f.read()
    except OSError:
        return True   # na dúvida, trata como IA


def username(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


# ---------------------------------------------------- protocolo legado
def legacy_classify(op_type, target_path=None):
    op = (op_type or "").lower()
    if op in P.CATEGORIES:
        return op
    if op in ("read", "list", "stat", "cat"):
        return "read"
    if op in ("write", "create", "mkdir"):
        if target_path and ("/home/" in target_path or "/var/lib/tupi-st/" in target_path):
            return "write_user_data"
        return "write_system_config"
    if op in ("install", "nix-build", "nix-env"):
        return "install_package"
    if op in ("curl", "wget", "fetch", "http"):
        return "network_egress"
    if op in ("exec", "run", "spawn"):
        return "execute_binary"
    if op in ("bootctl", "systemd-boot", "grub"):
        return "modify_bootloader"
    if op in ("sysfs", "dev", "hw"):
        return "access_hardware"
    if op in ("sudo", "su", "setuid", "cap_"):
        return "privilege_escalation"
    if op in ("rm", "delete", "unlink"):
        if target_path and ("/nix/store" in target_path or "/etc" in target_path):
            return "delete_system_file"
        return "write_user_data"
    return "write_system_config"


def legacy(req):
    agent_id = req.get("agent_id", "unknown")
    category = legacy_classify(req.get("operation", ""), req.get("target", ""))
    pol = load_json(LEGACY_POLICY, {"default_action": "deny", "rules": []})
    if AUTO_APPROVE_RO and category == "read":
        decision, reason = "allow", "auto-approved: read-only"
    else:
        action = pol.get("default_action", "deny")
        for rule in pol.get("rules", []):
            if rule.get("category") == category and (not rule.get("agents") or agent_id in rule["agents"]):
                action = rule.get("action", "deny")
                break
        decision = {"allow": "allow", "ask": "pending"}.get(action, "deny")
        reason = {"allow": "policy: explicit allow", "pending": "requires user approval"}.get(
            decision, f"policy: denied (category={category})")
    audit({"event": "legado", "agent_id": agent_id, "operation": req.get("operation", ""),
           "category": category, "decision": decision, "reason": reason})
    return {"decision": decision, "category": category, "reason": reason, "request_id": req.get("request_id", "")}


# ------------------------------------------------------- pedidos novos
def tool_decision(req, simulate=False):
    c = P.classify(req.get("tool", ""), req.get("args") or {}, req.get("home") or "/root")
    pol = current_policy()
    decision, reason = P.decide(c, pol)
    rid = uuid.uuid4().hex[:12]
    out = dict(c.to_dict(), decision=decision, reason=reason, request_id=rid, mode=pol["mode"],
               labels=[P.LABELS[x] for x in sorted(c.categories, key=P.CATEGORIES.index)])
    if not simulate:
        audit({"event": "decisao", "request_id": rid, "user": req.get("user"), "session": req.get("session"),
               "tool": c.tool, "summary": c.summary[:300], "categories": out["categories"], "risk": c.risk,
               "destructive": c.destructive, "decision": decision, "reason": reason, "mode": pol["mode"],
               "where": c.where, "agente": str(req.get("agente") or "Pajé")[:40]})
    return out


def policy_show():
    pol = current_policy()
    return dict(pol, categories=P.CATEGORIES, labels=P.LABELS, risk=P.RISK, critical=sorted(P.CRITICAL),
                explicit_only=sorted(P.EXPLICIT_ONLY), never_auto=sorted(P.NEVER_AUTO), modes=list(P.MODES),
                defaults={m: P.mode_table(m) for m in P.MODES},
                base=load_json(AGENT_BASE, {}), runtime=load_json(AGENT_RUNTIME, {}))


def write_runtime(data):
    tmp = AGENT_RUNTIME + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    try:
        import grp
        os.chown(tmp, 0, grp.getgrnam(GROUP).gr_gid)
    except (KeyError, PermissionError):
        pass
    os.chmod(tmp, 0o660)
    os.replace(tmp, AGENT_RUNTIME)


def policy_set(req, uid, pid):
    if in_ai_slice(pid):
        audit({"event": "politica_negada", "user": username(uid), "reason": "pedido veio de um processo da IA"})
        return {"ok": False, "error": "A IA não pode alterar a própria política."}
    with policy_lock:
        rt = load_json(AGENT_RUNTIME, {})
        changes = {}
        mode = req.get("mode")
        if mode == "padrao":
            # volta ao modo do NixOS (tupi.agent.mode); usado por "Desativar modo livre"
            rt.pop("mode", None)
            rt.pop("livre", None)
            changes["mode"] = "padrao"
        elif mode is not None:
            if mode not in P.MODES:
                return {"ok": False, "error": f"modo inválido: {mode}"}
            if mode == "livre":
                if req.get("confirm") != LIVRE_CONFIRM:
                    return {"ok": False, "error": f"Para o modo livre envie a confirmação \"{LIVRE_CONFIRM}\"."}
                scope = req.get("livre_scope", "1h")
                if scope not in ("1h", "boot", "sempre"):
                    return {"ok": False, "error": "prazo inválido (1h, boot ou sempre)"}
                lv = {"scope": scope, "since": time.time()}
                if scope == "1h":
                    lv["until"] = time.time() + 3600
                if scope == "boot":
                    lv["boot_id"] = boot_id()
                rt["livre"] = lv
            else:
                rt.pop("livre", None)
            rt["mode"] = mode
            changes["mode"] = mode
        if "overrides" in req:
            ov = dict(rt.get("overrides") or {})
            for cat, action in (req.get("overrides") or {}).items():
                if cat not in P.CATEGORIES:
                    return {"ok": False, "error": f"categoria inválida: {cat}"}
                if action in (None, "default", "padrao"):
                    ov.pop(cat, None)
                    continue
                if action not in P.ACTIONS:
                    return {"ok": False, "error": f"ação inválida: {action}"}
                if cat in P.NEVER_AUTO and action == "allow":
                    return {"ok": False, "error": f"\"{P.LABELS[cat]}\" sempre pergunta: só dá para escolher "
                                                  "Perguntar ou Negar."}
                elevated = (cat in P.CRITICAL or cat in P.EXPLICIT_ONLY) and action != "deny"
                if elevated and req.get("confirm") != CRITICAL_CONFIRM:
                    return {"ok": False, "error": f"Liberar \"{P.LABELS[cat]}\" exige a confirmação "
                                                  f"\"{CRITICAL_CONFIRM}\"."}
                ov[cat] = action
            rt["overrides"] = ov
            changes["overrides"] = req.get("overrides")
        rt["updated_by"] = username(uid)
        rt["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_runtime(rt)
    audit({"event": "politica_alterada", "user": username(uid), "changes": changes})
    return {"ok": True, "policy": policy_show()}


def policy_reset(uid, pid):
    if in_ai_slice(pid):
        return {"ok": False, "error": "A IA não pode alterar a própria política."}
    with policy_lock:
        write_runtime({"updated_by": username(uid), "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
    audit({"event": "politica_restaurada", "user": username(uid)})
    return {"ok": True, "policy": policy_show()}


def handle(req, pid, uid):
    t = req.get("type")
    if t is None:
        return legacy(req)
    if t == "tool_decide":
        if uid != 0:
            return {"decision": "deny", "reason": "só o executor da IA pode pedir decisões"}
        return tool_decision(req)
    if t == "tool_check":
        req = dict(req, home=req.get("home") or os.path.expanduser(f"~{username(uid)}"))
        return tool_decision(req, simulate=True)
    if t == "tool_event":
        if uid != 0:
            return {"ok": False, "error": "só o executor registra eventos"}
        ev = {k: v for k, v in req.items() if k != "type"}
        audit(dict(ev, event=ev.pop("event", "evento")))
        return {"ok": True}
    if t == "policy_show":
        return policy_show()
    if t == "policy_set":
        return policy_set(req, uid, pid)
    if t == "policy_reset":
        return policy_reset(uid, pid)
    return {"error": f"tipo desconhecido: {t}"}


# --------------------------------------------------------------- servidor
def read_request(conn):
    conn.settimeout(10)
    buf = b""
    while len(buf) < 4 * 1024 * 1024:
        try:
            chunk = conn.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        try:
            return json.loads(buf.decode("utf-8"))   # cliente antigo não fecha o envio
        except ValueError:
            continue
    return json.loads(buf.decode("utf-8") or "{}")


def serve(conn):
    try:
        pid, uid, _gid = peer(conn)
        req = read_request(conn)
        resp = handle(req, pid, uid)
    except Exception as e:  # nunca derruba o daemon
        resp = {"decision": "deny", "reason": f"error: {e}", "error": str(e)}
    try:
        conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
    except OSError:
        pass
    finally:
        conn.close()


def main():
    if os.path.exists(SOCKET_PATH):
        os.unlink(SOCKET_PATH)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o660)
    server.listen(MAX_CONCURRENT)
    print(f"[PermMgr] Listening on {SOCKET_PATH} | política: {current_policy()['mode']}", flush=True)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
