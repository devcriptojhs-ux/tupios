"""tupi-agent-loop — modo proativo da IA (serviço de usuário, sobe com a sessão gráfica).

Ciclo: observar (contagens locais) -> pensar (só RESUMOS vão para a API, dentro do limite diário de
tokens) -> propor (notificação + lista de sugestões no app). Nada é executado aqui: aplicar uma
sugestão passa pelo executor e pelo Permission Manager, como qualquer ação da IA.

Arquivos (do usuário):
  ~/.config/tupios-ia/loop.json         configurações (liga/desliga, intervalo, fontes, tokens/dia, silencioso)
  ~/.cache/tupios-ia/loop-status.json   estado atual (observando/pensando/pausado...) para a bolinha e o app
  ~/.local/share/tupios-ia/sugestoes.json
Memória (perfil e hábitos): SQLite do Agent Orchestrator, por usuário (MCP profile/* e observe/*).
"""
import glob
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime

from .tools import exec_op, request

HOME = os.path.expanduser("~")
CFG_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or HOME + "/.config", "tupios-ia", "loop.json")
STATUS_FILE = os.path.join(os.environ.get("XDG_CACHE_HOME") or HOME + "/.cache", "tupios-ia", "loop-status.json")
SUG_FILE = os.path.join(os.environ.get("XDG_DATA_HOME") or HOME + "/.local/share", "tupios-ia", "sugestoes.json")
MCP_SOCKET = os.environ.get("TUPI_MCP_SOCKET", "/run/tupi-agents/mcp.sock")
ROUTER = os.environ.get("TUPI_AI_URL", "http://127.0.0.1:8080").rstrip("/")

DEFAULTS = {
    "enabled": os.environ.get("TUPI_LOOP_ENABLED", "1") == "1",
    "interval_min": int(os.environ.get("TUPI_LOOP_INTERVAL", "15")),
    "sources": {"apps": True, "terminal": True, "files": True,
                "browser": os.environ.get("TUPI_LOOP_BROWSER", "0") == "1"},
    "daily_tokens": int(os.environ.get("TUPI_LOOP_DAILY_TOKENS", "20000")),
    "silent": os.environ.get("TUPI_LOOP_SILENT", "0") == "1",
}
IDLE_PAUSE_MS = 10 * 60 * 1000
ACTIVE_MS = 2 * 60 * 1000
SAMPLE_EVERY = 60


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


def config():
    cfg = json.loads(json.dumps(DEFAULTS))
    user = load_json(CFG_FILE, {})
    for k, v in user.items():
        if k == "sources" and isinstance(v, dict):
            cfg["sources"].update(v)
        elif k in cfg:
            cfg[k] = v
    cfg["interval_min"] = max(5, int(cfg["interval_min"]))
    return cfg


def status(state, detail="", **extra):
    st = load_json(STATUS_FILE, {})
    today = date.today().isoformat()
    if st.get("tokens_date") != today:
        st["tokens_date"], st["tokens_today"] = today, 0
    st.update(state=state, detail=detail, updated=time.time(), **extra)
    save_json(STATUS_FILE, st)
    return st


def mcp(method, **params):
    try:
        return request(MCP_SOCKET, {"method": method, "agent_id": "tupi-loop", "params": params}, 10)
    except (OSError, ValueError) as e:
        return {"error": str(e)}


def observe(kind, key, n=1):
    mcp("observe/add", kind=kind, key=key[:200], n=n)


# ------------------------------------------------------------- sinais
def idle_ms():
    try:
        return int(subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=5).stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def on_battery():
    for st in glob.glob("/sys/class/power_supply/*/status"):
        try:
            if open(st).read().strip() == "Discharging":
                return True
        except OSError:
            pass
    return False


def active_app():
    try:
        r = subprocess.run(["xdotool", "getactivewindow", "getwindowclassname"], capture_output=True, text=True,
                           timeout=5)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def norm_command(line):
    """Guarda só o 'formato' do comando (programa + subcomando), nunca argumentos/caminhos/segredos."""
    words = [w for w in re.split(r"\s+", line.strip()) if w]
    if not words or words[0].startswith("#"):
        return None
    while words and re.fullmatch(r"\w+=\S*", words[0]):
        words = words[1:]
    if not words:
        return None
    if words[0] in ("sudo", "doas", "time", "nohup") and len(words) > 1:
        words = words[1:]
    prog = os.path.basename(words[0])
    sub = words[1] if len(words) > 1 and re.fullmatch(r"[a-z][a-z-]{1,20}", words[1]) else ""
    return (prog + " " + sub).strip()[:60]


def scan_terminal(state):
    hist = os.path.join(HOME, ".bash_history")
    try:
        size = os.path.getsize(hist)
    except OSError:
        return 0
    pos = state.get("hist_pos", 0)
    if pos > size:
        pos = 0
    with open(hist, "rb") as f:
        f.seek(pos)
        lines = f.read().decode("utf-8", "replace").splitlines()
    state["hist_pos"] = size
    n = 0
    for line in lines:
        c = norm_command(line)
        if c:
            observe("comando", c)
            # repetição exata (só o hash), para sugerir atalhos sem guardar o texto
            observe("repeticao", hashlib.sha1(line.strip().encode()).hexdigest()[:12] + " " + c)
            n += 1
    return n


def scan_files(state):
    xbel = os.path.join(HOME, ".local/share/recently-used.xbel")
    try:
        mtime = os.path.getmtime(xbel)
        if mtime <= state.get("xbel_mtime", 0):
            return 0
        state["xbel_mtime"] = mtime
        root = ET.parse(xbel).getroot()
    except (OSError, ET.ParseError):
        return 0
    n = 0
    for b in root.iter("bookmark"):
        href = b.get("href", "")
        if not href.startswith("file://"):
            continue
        path = urllib.request.url2pathname(href[7:])
        ext = os.path.splitext(path)[1].lower() or "(sem extensão)"
        folder = os.path.dirname(path).replace(HOME, "~")
        observe("arquivo_tipo", ext)
        observe("pasta", folder)
        n += 1
    return n


def scan_errors(state):
    since = state.get("journal_since") or "-1h"
    state["journal_since"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for args in (["journalctl", "--user"], ["journalctl"]):
        try:
            r = subprocess.run(args + ["-p", "err", "--since", since, "-o", "json", "--no-pager", "-q"],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in r.stdout.splitlines()[:500]:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            unit = e.get("_SYSTEMD_UNIT") or e.get("_SYSTEMD_USER_UNIT") or e.get("SYSLOG_IDENTIFIER") or "?"
            observe("erro", unit)
            n += 1
    return n


def scan_browser(state):
    n = 0
    for db in glob.glob(os.path.join(HOME, ".mozilla/firefox/*/places.sqlite")):
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "p.sqlite")
            try:
                shutil.copy(db, copy)
                con = sqlite3.connect(copy)
                since = int((time.time() - 86400) * 1e6)
                rows = con.execute("SELECT p.url FROM moz_historyvisits v JOIN moz_places p ON p.id = v.place_id "
                                   "WHERE v.visit_date > ?", (since,)).fetchall()
                con.close()
            except (OSError, sqlite3.Error):
                continue
        for (url,) in rows:
            m = re.match(r"https?://([^/]+)", url or "")
            if m:
                observe("site", m.group(1))   # só o domínio
                n += 1
    return n


# -------------------------------------------------------------- pensar
def top(kind, limit=8):
    r = mcp("observe/top", kind=kind, limit=limit)
    return [(x["key"], x["count"]) for x in r.get("items", [])] if isinstance(r, dict) else []


def build_summary(cfg):
    parts = []
    hour = datetime.now().hour
    if cfg["sources"].get("apps"):
        parts.append("Apps mais usados (amostras de 1 min): " + ", ".join(f"{k} ({c})" for k, c in top("app")))
        parts.append(f"Hora atual: {hour}h. Apps nesta faixa: " +
                     ", ".join(f"{k} ({c})" for k, c in top(f"app_hora_{hour:02d}", 5)))
    if cfg["sources"].get("terminal"):
        parts.append("Comandos mais usados (só programa+subcomando): " +
                     ", ".join(f"{k} ({c})" for k, c in top("comando", 10)))
        reps = [(k.split(" ", 1)[1], c) for k, c in top("repeticao", 5) if c >= 5]
        if reps:
            parts.append("Comandos repetidos muitas vezes: " + ", ".join(f"{k} ({c}x)" for k, c in reps))
    if cfg["sources"].get("files"):
        parts.append("Tipos de arquivo recentes: " + ", ".join(f"{k} ({c})" for k, c in top("arquivo_tipo", 6)))
        parts.append("Pastas recentes: " + ", ".join(f"{k} ({c})" for k, c in top("pasta", 5)))
    if cfg["sources"].get("browser"):
        parts.append("Sites mais visitados (domínios): " + ", ".join(f"{k} ({c})" for k, c in top("site", 6)))
    errs = top("erro", 5)
    if errs:
        parts.append("Erros nos logs desde o último ciclo: " + ", ".join(f"{k} ({c})" for k, c in errs))
    facts = mcp("profile/list").get("items", [])
    if facts:
        parts.append("O que você já sabe sobre o usuário: " + "; ".join(f["value"] for f in facts[:20]))
    return "\n".join(p for p in parts if not p.endswith(": "))


PROMPT = (
    "Você é o modo proativo do Pajé (a IA do TUPIOS). Com base no RESUMO de uso abaixo (contagens locais, sem conteúdo "
    "privado), proponha no máximo 3 ajudas concretas e úteis: automatizar uma repetição, avisar de um "
    "problema nos logs, sugerir um atalho ou preparar o ambiente do próximo trabalho. Só proponha se houver "
    "um motivo claro no resumo. Responda SOMENTE com JSON válido, sem texto fora dele, no formato:\n"
    '{"fatos": ["frase curta sobre um hábito do usuário"], "sugestoes": [{"titulo": "...", "motivo": "...", '
    '"tipo": "dica|alerta|atalho|script|pacote", "acao": {"tool": "nome", "args": {}} | null, '
    '"arquivo": {"path": "~/.local/bin/nome", "content": "..."} | null}]}\n'
    "Ferramentas possíveis em 'acao': run_command, write_file, install_package, open_app, manage_service. "
    "Para criar um script/atalho use 'arquivo' (o usuário verá o conteúdo antes de aprovar)."
)


def think(cfg, summary):
    st = status("pensando", "analisando seus hábitos")
    budget = cfg["daily_tokens"] - st.get("tokens_today", 0)
    if budget < 1500:
        return status("ocioso", "limite diário de tokens atingido; volto amanhã")
    body = {"messages": [{"role": "system", "content": PROMPT}, {"role": "user", "content": summary}],
            "max_tokens": min(1200, budget - 300)}
    req = urllib.request.Request(ROUTER + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.load(r)
    except Exception as e:
        return status("ocioso", f"não consegui falar com a IA: {e}")
    usage = data.get("usage") or {}
    spent = usage.get("total_tokens") or (len(PROMPT) + len(summary)) // 3 + 600
    st = status("pensando", "lendo a resposta", tokens_today=st.get("tokens_today", 0) + spent)
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    m = re.search(r"\{.*\}", text, re.S)
    try:
        plan = json.loads(m.group(0)) if m else {}
    except ValueError:
        plan = {}
    for fact in (plan.get("fatos") or [])[:5]:
        if isinstance(fact, str) and fact.strip():
            mcp("profile/set", key="habito:" + hashlib.sha1(fact.encode()).hexdigest()[:10], value=fact[:300],
                source="modo proativo")
    sugs = load_json(SUG_FILE, [])
    known = {s.get("titulo") for s in sugs if s.get("status") == "nova"}
    new = []
    for s in (plan.get("sugestoes") or [])[:3]:
        if not isinstance(s, dict) or not s.get("titulo") or s["titulo"] in known:
            continue
        s.update(id=uuid.uuid4().hex[:10], status="nova", criada=time.time())
        new.append(s)
    if new:
        save_json(SUG_FILE, (sugs + new)[-50:])
        if not cfg["silent"]:
            notify(new)
    return status("ocioso", f"{len(new)} sugestão(ões) nova(s)" if new else "nada novo para sugerir",
                  last_cycle=time.time())


def notify(new):
    title = "O Pajé tem " + ("uma sugestão" if len(new) == 1 else f"{len(new)} sugestões")
    body = "\n".join("• " + s["titulo"] for s in new)
    try:
        subprocess.run(["notify-send", "-a", "Pajé", "-i", "tupios-ia", title, body], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


# ---------------------------------------------------------------- laço
def main():
    state = {}
    last_cycle = 0.0
    last_sample = 0.0
    status("ocioso", "iniciando")
    while True:
        cfg = config()
        if not cfg["enabled"]:
            status("desligado", "modo proativo desligado nas configurações")
            time.sleep(60)
            continue
        st = exec_op("status", 10)
        if st.get("halted"):
            status("parado", "IA PARADA (PARAR IA)")
            time.sleep(30)
            continue
        idle = idle_ms()
        if on_battery():
            status("pausado", "na bateria")
            time.sleep(60)
            continue
        if idle > IDLE_PAUSE_MS:
            status("pausado", "você está ausente")
            time.sleep(60)
            continue
        now = time.time()
        if cfg["sources"].get("apps") and now - last_sample >= SAMPLE_EVERY:
            app = active_app()
            if app:
                observe("app", app)
                observe(f"app_hora_{datetime.now().hour:02d}", app)
            last_sample = now
        interval = cfg["interval_min"] * 60
        if idle < ACTIVE_MS:
            interval = max(300, interval // 2)   # usuário ativo: ciclos mais frequentes
        if now - last_cycle >= interval:
            status("observando", "lendo sinais de uso")
            if cfg["sources"].get("terminal"):
                scan_terminal(state)
            if cfg["sources"].get("files"):
                scan_files(state)
            if cfg["sources"].get("browser"):
                scan_browser(state)
            scan_errors(state)
            summary = build_summary(cfg)
            r = think(cfg, summary) if summary.strip() else {}
            last_cycle = time.time()
            if str((r or {}).get("detail", "")).startswith("não consegui falar com a IA"):
                # IA ainda não está no ar (boot, rede, chave): tenta de novo em 2 min, não no próximo ciclo
                last_cycle -= max(0, interval - 120)
            status(load_json(STATUS_FILE, {}).get("state", "ocioso"), load_json(STATUS_FILE, {}).get("detail", ""),
                   next_cycle=last_cycle + interval)
        time.sleep(20)


if __name__ == "__main__":
    main()
