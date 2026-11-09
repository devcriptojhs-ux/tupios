"""tupi-paje — motor de orquestração do Pajé (serviço de usuário, sem privilégios).

O Pajé (IA principal, no app) delega TRABALHOS a SUBAGENTES. Este serviço guarda as almas e os trabalhos,
roda vários trabalhos em PARALELO (cada um é um laço de agente com a alma, as ferramentas e o modelo do
subagente) e devolve os relatórios ao Pajé. Inspirado em times de agentes (organograma, tarefas, orçamento,
rotinas), mas sob as regras do TUPIOS:

  - toda ação de sistema de um subagente vai ao executor/Permission Manager (com o nome do agente na
    aprovação e na auditoria); a alma nunca dá permissão;
  - subagentes não criam agentes, não delegam e não aplicam nada no PD;
  - cada agente tem orçamento diário de tokens; PARAR IA cancela tudo; rotinas são opcionais.

API local (JSON em socket Unix só do usuário): ver HARNESS.md.
"""
import base64
import json
import os
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque

from . import souls as S
from .tools import COMPUTER_TOOLS, ORCH_SCHEMAS, TOOL_SCHEMAS, exec_op, paje_socket, result_for_model

ROUTER = os.environ.get("TUPI_AI_URL", "http://127.0.0.1:8080").rstrip("/")
MAX_PARALLEL = int(os.environ.get("TUPI_PAJE_MAX_PARALLEL", "3"))
MAX_SUBAGENTS = int(os.environ.get("TUPI_PAJE_MAX_SUBAGENTS", "12"))
MAX_QUEUE = int(os.environ.get("TUPI_PAJE_MAX_QUEUE", "50"))
MAX_STEPS = int(os.environ.get("TUPI_PAJE_MAX_STEPS", "12"))
# usar o mouse/teclado leva muitos passos curtos (print -> ação -> print)
MAX_STEPS_COMPUTER = int(os.environ.get("TUPI_PAJE_MAX_STEPS_COMPUTER", "40"))
CHAT_TIMEOUT = int(os.environ.get("TUPI_PAJE_CHAT_TIMEOUT", "330"))
HALT_POLL = float(os.environ.get("TUPI_PAJE_HALT_POLL", "3"))
DOC = os.environ.get("TUPI_PAJE_DOC", "/etc/tupi/paje/HARNESS.md")
ACTIVE = ("na_fila", "rodando")
DONE = ("concluida", "falhou", "cancelada")


def now():
    return time.time()


def today():
    return time.strftime("%Y-%m-%d")


def tasks_file():
    return os.path.join(S.DATA, "trabalhos.json")


def state_file():
    return os.path.join(S.DATA, "estado.json")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def short(text, n=300):
    text = "" if text is None else str(text)
    return text if len(text) <= n else text[:n] + "…"


def router_chat(payload):
    """POST /v1/chat/completions no AI Router (substituível nos testes)."""
    req = urllib.request.Request(ROUTER + "/v1/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=CHAT_TIMEOUT) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(raw).get("error", raw)
            raw = err.get("message", err) if isinstance(err, dict) else err
        except ValueError:
            pass
        raise RuntimeError(f"IA respondeu HTTP {e.code}: {short(raw, 400)}")
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError(f"não consegui falar com o AI Router: {getattr(e, 'reason', e)}")


KEEP_SCREENSHOTS = 2          # só os últimos prints vão como imagem (os antigos viram texto: economiza tokens)
IMAGE_TOKENS = 1500           # quanto um print 1280x800 custa, mais ou menos, nos provedores de visão


def estimate_tokens(payload):
    """Quando o provedor não informa o uso: ~4 caracteres por token no texto e um valor fixo por imagem
    (o base64 da imagem NÃO é texto para o modelo)."""
    images = 0
    msgs = []
    for m in payload.get("messages", []):
        if isinstance(m.get("content"), list):
            parts = []
            for p in m["content"]:
                if isinstance(p, dict) and p.get("type") == "image_url":
                    images += 1
                else:
                    parts.append(p)
            m = dict(m, content=parts)
        msgs.append(m)
    return len(json.dumps(dict(payload, messages=msgs), ensure_ascii=False)) // 4 + images * IMAGE_TOKENS


def add_screenshots(messages, paths):
    """Prints da tela que o subagente pediu -> imagem na conversa dele (mensagem do usuário, depois das tools)."""
    for path in paths:
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            continue
        messages.append({"role": "user", "content": [
            {"type": "text", "text": "(print da tela que você pediu; coordenadas nesta imagem)"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64," + data}}]})
    imgs = [m for m in messages if m.get("role") == "user" and isinstance(m.get("content"), list)
            and any(p.get("type") == "image_url" for p in m["content"] if isinstance(p, dict))]
    for m in imgs[:-KEEP_SCREENSHOTS]:
        m["content"] = "(print antigo da tela, removido para economizar; veja o mais recente)"


def session_display():
    """DISPLAY/XAUTHORITY/XDG_CURRENT_DESKTOP da sessão gráfica. O serviço de usuário pode ter subido antes do X:
    nesse caso pega do ambiente do systemd --user (a sessão gráfica importa essas variáveis nele)."""
    keys = ("DISPLAY", "XAUTHORITY", "XDG_CURRENT_DESKTOP")
    if os.environ.get("DISPLAY"):
        return tuple(os.environ.get(k) for k in keys)
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                             timeout=5).stdout
        env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return tuple(env.get(k) for k in keys)
    except (OSError, subprocess.SubprocessError):
        return None, None, None


def router_health():
    try:
        with urllib.request.urlopen(ROUTER + "/health", timeout=5) as r:
            return json.load(r)
    except (OSError, ValueError, urllib.error.URLError):
        return {}


def log_event(evento, **fields):
    """Auditoria (o executor grava no log append-only; o motor não tem acesso direto)."""
    try:
        exec_op("log", 5, evento=evento, **{k: short(v, 300) for k, v in fields.items()})
    except Exception:
        pass


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.tasks = {}
        self.queue = deque()
        self.cancel = {}
        self.running = set()
        self.state = load_json(state_file(), {})
        for t in load_json(tasks_file(), []):
            if t.get("status") in ACTIVE:
                t.update(status="falhou", erro="interrompido: o motor do Pajé reiniciou", fim=now())
            self.tasks[t["id"]] = t

    # ----------------------------------------------------------- estado
    def save(self):
        with self.lock:
            items = sorted(self.tasks.values(), key=lambda t: t["criado"])[-300:]
            self.tasks = {t["id"]: t for t in items}
            save_json(tasks_file(), items)
            save_json(state_file(), self.state)

    def tokens_today(self, agent_id):
        return int(((self.state.get("uso") or {}).get(today()) or {}).get(agent_id, 0))

    def add_tokens(self, agent_id, n):
        with self.lock:
            uso = self.state.setdefault("uso", {})
            for d in list(uso):
                if d != today():
                    uso.pop(d)
            day = uso.setdefault(today(), {})
            day[agent_id] = day.get(agent_id, 0) + int(n)

    @staticmethod
    def ia_halted():
        """PARAR IA ligado no executor? (sem executor: não dá para saber; as ações falham de qualquer jeito)"""
        try:
            return bool(exec_op("status", 5).get("halted"))
        except Exception:
            return False

    def resolve_agent(self, ref):
        ref = str(ref or "").strip()
        agents = S.list_agents()
        for a in agents:
            if ref in (a["id"], a["nome"]) or S.slugify(ref) == a["id"] or ref.lower() == a["nome"].lower():
                return a
        raise ValueError(f"subagente não encontrado: {ref!r}. Existem: " +
                         (", ".join(a["nome"] for a in agents) or "nenhum (crie com criar_subagente)"))

    # ------------------------------------------------------- trabalhos
    def create_task(self, agente, titulo, instrucoes, origem="usuario", conversa=None, rotina=False):
        a = self.resolve_agent(agente)
        titulo = short(str(titulo or "").strip() or "(sem título)", 200)
        instrucoes = str(instrucoes or "").strip()[:20000]
        if not instrucoes:
            raise ValueError("o trabalho precisa de instruções")
        if self.ia_halted():
            raise ValueError("a IA está parada (PARAR IA): retome a IA antes de dar trabalhos aos subagentes")
        with self.lock:
            if sum(1 for t in self.tasks.values() if t["status"] in ACTIVE) >= MAX_QUEUE:
                raise ValueError(f"já há {MAX_QUEUE} trabalhos na fila/rodando: espere terminarem")
            t = {"id": uuid.uuid4().hex[:10], "agente": a["id"], "agente_nome": a["nome"], "titulo": titulo,
                 "instrucoes": instrucoes, "origem": origem if origem in ("usuario", "paje", "rotina") else "usuario",
                 "conversa": conversa, "rotina": bool(rotina), "status": "na_fila", "criado": now(), "inicio": None,
                 "fim": None, "resultado": None, "erro": None, "passos": [], "tokens": 0,
                 "conexao": a["conexao"], "modelo": a["modelo"]}
            self.tasks[t["id"]] = t
            self.cancel[t["id"]] = threading.Event()
            self.queue.append(t["id"])
            self.save()
            self.cond.notify_all()
        log_event("trabalho_criado", trabalho=t["id"], agente=a["nome"], titulo=titulo, origem=t["origem"])
        return t

    def cancel_task(self, task_id, why="cancelado pelo usuário"):
        with self.lock:
            t = self.tasks.get(task_id)
            if not t:
                raise ValueError("trabalho não encontrado")
            if t["status"] not in ACTIVE:
                return t
            ev = self.cancel.setdefault(task_id, threading.Event())
            ev.set()
            if t["status"] == "na_fila":
                if task_id in self.queue:
                    self.queue.remove(task_id)
                t.update(status="cancelada", erro=why, fim=now())
                self.cond.notify_all()
            else:
                t["cancelando"] = why
            self.save()
            return t

    def cancel_all(self, why):
        with self.lock:
            ids = [t["id"] for t in self.tasks.values() if t["status"] in ACTIVE]
        for i in ids:
            self.cancel_task(i, why)
        return len(ids)

    def next_task(self):
        with self.lock:
            while True:
                if self.queue and len(self.running) < MAX_PARALLEL:
                    tid = self.queue.popleft()
                    t = self.tasks.get(tid)
                    if t and t["status"] == "na_fila":
                        t.update(status="rodando", inicio=now())
                        self.running.add(tid)
                        self.save()
                        self.cond.notify_all()
                        return t
                    continue
                self.cond.wait(2)

    def worker(self):
        while True:
            t = self.next_task()
            try:
                self.run_task(t)
            except Exception as e:      # o laço nunca derruba o motor
                with self.lock:
                    t.update(status="falhou", erro=f"erro interno: {e}", fim=now())
            finally:
                with self.lock:
                    self.running.discard(t["id"])
                    self.save()
                    self.cond.notify_all()
                log_event("trabalho_concluido", trabalho=t["id"], agente=t["agente_nome"], status=t["status"],
                          tokens=t["tokens"])

    def step(self, t, **kw):
        with self.lock:
            t["passos"].append(dict(kw, quando=now()))
            self.save()

    def run_task(self, t):
        ev = self.cancel.setdefault(t["id"], threading.Event())
        try:
            a = S.load_agent(t["agente"])
        except (OSError, ValueError):
            t.update(status="falhou", erro="o subagente não existe mais", fim=now())
            return
        if self.tokens_today(a["id"]) >= a["orcamento_tokens_dia"]:
            t.update(status="falhou", erro=f"orçamento diário de {a['orcamento_tokens_dia']} tokens de {a['nome']} "
                                          "esgotado (ajuste na alma do agente)", fim=now())
            return
        tools = S.sub_tool_schemas(a)
        allowed = set(a["ferramentas"])
        messages = [{"role": "system", "content": S.sub_system_prompt(a)},
                    {"role": "user", "content": f"Tarefa do Pajé: {t['titulo']}\n\n{t['instrucoes']}"}]
        steps = MAX_STEPS_COMPUTER if allowed & COMPUTER_TOOLS else MAX_STEPS
        for _ in range(steps):
            if ev.is_set():
                t.update(status="cancelada", erro=t.get("cancelando") or "cancelado", fim=now())
                return
            payload = {"messages": messages}
            if tools:
                payload.update(tools=tools, tool_choice="auto")
            if a["modelo"]:
                payload["model"] = a["modelo"]
            if a["conexao"] and a["conexao"] != "principal":
                payload["tupi_conexao"] = a["conexao"]
            try:
                data = router_chat(payload)
            except RuntimeError as e:
                t.update(status="falhou", erro=str(e), fim=now())
                return
            usage = (data.get("usage") or {}).get("total_tokens") or estimate_tokens(payload)
            t["tokens"] += int(usage)
            self.add_tokens(a["id"], usage)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            calls = msg.get("tool_calls") or []
            api = {k: msg[k] for k in ("content", "tool_calls", "tupi_raw") if k in msg}
            api["role"] = "assistant"
            messages.append(api)
            text = msg.get("content") or ""
            if isinstance(text, list):
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            if not calls:
                t.update(status="concluida", resultado=text.strip() or "(o subagente não escreveu relatório)",
                         fim=now(), modelo_usado=data.get("model"))
                return
            shots = []
            for tc in calls:
                f = tc.get("function") or {}
                name = f.get("name") or ""
                try:
                    args = json.loads(f.get("arguments") or "{}")
                except ValueError:
                    args = {}
                if ev.is_set():
                    break
                if name not in allowed:
                    content, is_err = (f"Ferramenta {name!r} não é permitida para {a['nome']} (a alma define: "
                                       f"{', '.join(sorted(allowed)) or 'nenhuma'})."), True
                    self.step(t, ferramenta=name, status="negado", resumo="ferramenta fora da alma", onde="-")
                else:
                    self.step(t, ferramenta=name, status="rodando", resumo=short(json.dumps(args, ensure_ascii=False),
                                                                                  200))
                    display, xauth, desktop = session_display()
                    r = exec_op("run", tool=name, args=args, session=f"tarefa-{t['id']}", agente=a["nome"],
                                display=display, xauthority=xauth, desktop=desktop)
                    content, is_err = result_for_model(r)
                    res = r.get("result") if isinstance(r.get("result"), dict) else {}
                    if name in ("use_computer", "take_screenshot") and r.get("status") == "ok" and res.get("arquivo"):
                        shots.append(res["arquivo"])
                    with self.lock:
                        t["passos"][-1].update(status=r.get("status") or "error", resumo=r.get("summary") or name,
                                               onde=r.get("onde"), motivo=r.get("reason") or r.get("error"),
                                               aprovado_por=r.get("approved_by"), codigo=r.get("exit_code"))
                        self.save()
                    if r.get("status") != "ok" and self.ia_halted():
                        # PARAR IA: o trabalho termina AQUI (sem dar ao modelo a chance de "concluir" mesmo assim)
                        ev.set()
                        t.update(status="cancelada", erro="cancelado pelo PARAR IA", fim=now())
                        return
                messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": content,
                                 **({"tupi_is_error": True} if is_err else {})})
            add_screenshots(messages, shots)
        if ev.is_set():
            t.update(status="cancelada", erro=t.get("cancelando") or "cancelado", fim=now())
        else:
            t.update(status="falhou", erro=f"passou do limite de {steps} passos sem concluir", fim=now())

    def wait(self, ids, timeout):
        end = now() + max(0, min(float(timeout), 900))
        with self.lock:
            while True:
                pend = [i for i in ids if self.tasks.get(i, {}).get("status") in ACTIVE]
                if not pend or now() >= end:
                    break
                self.cond.wait(min(2, max(0.05, end - now())))
            return [self.public(self.tasks[i]) for i in ids if i in self.tasks]

    # --------------------------------------------------- vigias
    def halt_watch(self):
        """PARAR IA (executor parado) cancela todos os trabalhos."""
        while True:
            time.sleep(HALT_POLL)
            try:
                st = exec_op("status", 5)
            except Exception:
                continue
            if st.get("halted"):
                n = self.cancel_all("cancelado pelo PARAR IA")
                if n:
                    log_event("trabalhos_cancelados", motivo="PARAR IA", quantos=n)

    def routines(self):
        """Rotinas: trabalhos periódicos definidos na alma (rotina_minutos + rotina)."""
        while True:
            time.sleep(60)
            try:
                halted = exec_op("status", 5).get("halted")
            except Exception:
                halted = False
            if halted:
                continue
            for a in S.list_agents():
                if not (a["rotina_minutos"] and a["rotina"].strip()):
                    continue
                last = (self.state.get("rotinas") or {}).get(a["id"], 0)
                busy = any(t["agente"] == a["id"] and t["rotina"] and t["status"] in ACTIVE
                           for t in list(self.tasks.values()))
                if busy or now() - last < max(15, a["rotina_minutos"]) * 60:
                    continue
                with self.lock:
                    self.state.setdefault("rotinas", {})[a["id"]] = now()
                try:
                    self.create_task(a["id"], f"Rotina de {a['nome']}", a["rotina"], origem="rotina", rotina=True)
                except ValueError:
                    pass

    # --------------------------------------------------------- leitura
    def pending_sessions(self):
        try:
            return {p.get("session") for p in exec_op("pending", 5).get("pending", [])}
        except Exception:
            return set()

    def public(self, t, pend=None):
        out = {k: v for k, v in t.items() if k != "instrucoes"}
        out["instrucoes"] = short(t.get("instrucoes"), 2000)
        if t["status"] == "rodando" and pend is not None and f"tarefa-{t['id']}" in pend:
            out["aguardando_aprovacao"] = True
        return out

    def agents_public(self):
        with self.lock:
            active = [t for t in self.tasks.values() if t["status"] in ACTIVE]
        out = []
        for a in S.list_agents():
            mine = [t for t in active if t["agente"] == a["id"]]
            out.append(dict({k: v for k, v in a.items() if k != "alma"},
                            estado="trabalhando" if any(t["status"] == "rodando" for t in mine) else
                            ("na fila" if mine else "ocioso"),
                            trabalhos_ativos=len(mine), tokens_hoje=self.tokens_today(a["id"])))
        return out


E = None


# --------------------------------------------------------------- API
def handle(req):
    m, p = req.get("method"), req.get("params") or {}
    if m == "status":
        with E.lock:
            ts = list(E.tasks.values())
        return {"ok": True, "max_paralelo": MAX_PARALLEL, "rodando": sum(t["status"] == "rodando" for t in ts),
                "na_fila": sum(t["status"] == "na_fila" for t in ts), "agentes": len(S.list_agents()),
                "max_subagentes": MAX_SUBAGENTS}
    if m == "agents/list":
        return {"agentes": E.agents_public(), "max_subagentes": MAX_SUBAGENTS}
    if m == "agents/get":
        return {"agente": S.load_agent(p.get("id"))}
    if m == "agents/templates":
        return {"modelos": [{"chave": k, "nome": v[0], "papel": v[1], "ferramentas": v[2], "alma": v[3]}
                            for k, v in S.TEMPLATES.items()], "ferramentas": S.SUB_TOOLS}
    if m == "agents/create":
        if len(S.list_agents()) >= MAX_SUBAGENTS:
            raise ValueError(f"limite de {MAX_SUBAGENTS} subagentes atingido: apague um antes")
        tpl = S.TEMPLATES.get(str(p.get("modelo_base") or ""))
        meta = {"nome": p.get("nome") or (tpl[0] if tpl else ""), "papel": p.get("papel") or (tpl[1] if tpl else ""),
                "ferramentas": p.get("ferramentas") if p.get("ferramentas") is not None else (tpl[2] if tpl else None),
                "conexao": p.get("conexao"), "modelo": p.get("modelo"),
                # usar o computador manda prints (imagens): orçamento padrão maior
                "orcamento_tokens_dia": p.get("orcamento_tokens_dia") or (
                    S.DEFAULT_BUDGET * 4 if set((p.get("ferramentas") if p.get("ferramentas") is not None else
                                                 (tpl[2] if tpl else [])) or []) & COMPUTER_TOOLS
                    else S.DEFAULT_BUDGET),
                "rotina_minutos": p.get("rotina_minutos") or 0, "rotina": p.get("rotina") or "",
                "criado_por": "paje" if p.get("criado_por") == "paje" else "usuario"}
        a = S.normalize_agent(meta, p.get("alma") or (tpl[3] if tpl else ""))
        if os.path.exists(S.soul_path(a["id"])):
            raise ValueError(f"já existe um subagente com esse nome ({a['nome']})")
        S.save_agent(a)
        log_event("agente_criado", agente=a["nome"], por=a["criado_por"], ferramentas=",".join(a["ferramentas"]))
        return {"ok": True, "agente": a}
    if m == "agents/update":
        old = S.load_agent(p.get("id"))
        if p.get("texto") is not None:                # a alma inteira (.soul.md) editada
            meta, body = S.parse(p["texto"])
            meta.setdefault("criado_por", old["criado_por"])
            meta.setdefault("criado_em", old["criado_em"])
            a = S.normalize_agent(meta, body, old["id"])
        else:
            merged = dict(old)
            merged.update({k: v for k, v in p.items() if k in S.FIELDS or k == "alma"})
            a = S.normalize_agent(merged, merged.get("alma"), old["id"])
        S.save_agent(a)
        log_event("agente_editado", agente=a["nome"])
        return {"ok": True, "agente": a}
    if m == "agents/delete":
        a = S.load_agent(p.get("id"))
        with E.lock:
            ids = [t["id"] for t in E.tasks.values() if t["agente"] == a["id"] and t["status"] in ACTIVE]
        for i in ids:
            E.cancel_task(i, "o subagente foi apagado")
        S.delete_agent(a["id"])
        log_event("agente_removido", agente=a["nome"])
        return {"ok": True}
    if m == "tasks/create":
        return {"ok": True, "trabalho": E.public(E.create_task(p.get("agente"), p.get("titulo"), p.get("instrucoes"),
                                                               p.get("origem") or "usuario", p.get("conversa")))}
    if m == "tasks/create_many":
        out = []
        for item in (p.get("tarefas") or [])[:20]:
            try:
                t = E.create_task(item.get("agente"), item.get("titulo"), item.get("instrucoes"),
                                  p.get("origem") or "paje", p.get("conversa"))
                out.append({"id": t["id"], "agente": t["agente_nome"], "titulo": t["titulo"], "status": t["status"]})
            except ValueError as e:
                out.append({"erro": str(e), "agente": item.get("agente"), "titulo": item.get("titulo")})
        return {"ok": any("id" in o for o in out), "trabalhos": out,
                "paralelo": f"até {MAX_PARALLEL} rodam ao mesmo tempo"}
    if m == "tasks/list":
        pend = E.pending_sessions()
        with E.lock:
            ts = sorted(E.tasks.values(), key=lambda t: t["criado"], reverse=True)
        if p.get("conversa"):
            ts = [t for t in ts if t.get("conversa") == p["conversa"]]
        if p.get("ids"):
            ts = [t for t in ts if t["id"] in p["ids"]]
        return {"trabalhos": [E.public(t, pend) for t in ts[:int(p.get("limite") or 200)]]}
    if m == "tasks/get":
        t = E.tasks.get(p.get("id"))
        if not t:
            raise ValueError("trabalho não encontrado")
        return {"trabalho": dict(E.public(t, E.pending_sessions()), instrucoes=t["instrucoes"])}
    if m == "tasks/wait":
        ids = p.get("ids") or [t["id"] for t in list(E.tasks.values()) if t.get("conversa") == p.get("conversa")
                               and p.get("conversa")]
        seg = p.get("segundos")
        res = E.wait(ids, 30 if seg is None else seg)
        return {"trabalhos": res, "terminados": all(t["status"] in DONE for t in res)}
    if m == "tasks/cancel":
        return {"ok": True, "trabalho": E.public(E.cancel_task(p.get("id")))}
    if m == "tasks/cancel_all":
        return {"ok": True, "cancelados": E.cancel_all(p.get("motivo") or "cancelado pelo usuário")}
    if m == "paje/soul":
        if p.get("texto") is not None:
            s = S.save_paje_soul(p["texto"])
            log_event("alma_do_paje_editada")
            return {"ok": True, "alma": s}
        return {"alma": S.load_paje_soul()}
    if m == "harness":
        h = router_health()
        return {"constituicao_paje": S.CONSTITUICAO_PAJE, "constituicao_subagente": S.CONSTITUICAO_SUB,
                "alma_paje": S.load_paje_soul(),
                "ferramentas_sistema": [{"nome": t["function"]["name"], "descricao": t["function"]["description"]}
                                        for t in TOOL_SCHEMAS],
                "ferramentas_orquestracao": [{"nome": t["function"]["name"], "descricao": t["function"]["description"]}
                                             for t in ORCH_SCHEMAS],
                "ferramentas_de_subagente": S.SUB_TOOLS,
                "conexoes": h.get("conexoes") or ([{"nome": "principal", "provider": h.get("provider"),
                                                    "model": h.get("model"), "configured": h.get("configured")}]
                                                  if h else []),
                "limites": {"max_paralelo": MAX_PARALLEL, "max_subagentes": MAX_SUBAGENTS, "max_fila": MAX_QUEUE,
                            "max_passos": MAX_STEPS, "orcamento_padrao": S.DEFAULT_BUDGET},
                "arquivos": {"pasta": S.DATA, "alma_paje": S.paje_soul_path(), "agentes": S.agents_dir(),
                             "documentacao": DOC if os.path.exists(DOC) else None,
                             "trabalhos": tasks_file()},
                "socket": paje_socket()}
    raise ValueError(f"método desconhecido: {m}")


def serve(conn):
    try:
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                                              struct.calcsize("3i")))
        buf = b""
        conn.settimeout(15)
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
            if len(buf) > 4 * 1024 * 1024:
                break
        conn.settimeout(None)
        if uid != os.getuid():
            resp = {"error": "o motor do Pajé só atende o próprio usuário"}
        else:
            try:
                resp = handle(json.loads(buf.decode("utf-8") or "{}"))
            except (ValueError, OSError, KeyError) as e:
                resp = {"error": str(e)}
        conn.sendall(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
    except Exception as e:
        try:
            conn.sendall(json.dumps({"error": f"motor do Pajé: {e}"}).encode("utf-8"))
        except OSError:
            pass
    finally:
        conn.close()


def start():
    """Liga o motor (workers + vigias). Separado de main() para os testes."""
    global E
    os.makedirs(S.agents_dir(), exist_ok=True)
    if not os.path.exists(S.paje_soul_path()):
        S.save_paje_soul(S.PAJE_SOUL_DEFAULT)
    E = Engine()
    for _ in range(MAX_PARALLEL):
        threading.Thread(target=E.worker, daemon=True).start()
    threading.Thread(target=E.halt_watch, daemon=True).start()
    threading.Thread(target=E.routines, daemon=True).start()
    return E


def main():
    start()
    path = paje_socket()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if os.path.exists(path):
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(32)
    print(f"[Pajé] motor no ar em {path} | até {MAX_PARALLEL} trabalhos em paralelo | "
          f"{len(S.list_agents())} subagentes", flush=True)
    while True:
        c, _ = srv.accept()
        threading.Thread(target=serve, args=(c,), daemon=True).start()


if __name__ == "__main__":
    main()
