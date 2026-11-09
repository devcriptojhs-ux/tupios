#!/usr/bin/env python3
"""Pajé (TUPIOS) — assistente no estilo ChatGPT + bolinha flutuante.

Um único processo por sessão gráfica (Gtk.Application):
  - servidor HTTP local (127.0.0.1, porta aleatória, protegido por token) com a
    interface web (ui/) e a API: conversas, anexos, gravação de voz, prints;
  - bolinha flutuante sempre visível (arrastável); clique = mini janela de chat;
  - janela completa "Pajé" (aparece na barra de tarefas).
A IA em si é acessada pelo tupi-ai-router (http://127.0.0.1:8080, formato OpenAI).
"""
import os
import sys

# WebKitGTK em VMs / GPUs sem aceleração: sem isto a janela pode abrir em branco.
os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
# Bolinha "sempre visível", posição e print da tela dependem do X11 (XFCE).
# Numa sessão Wayland com XWayland, usa o X11 também.
if os.environ.get("DISPLAY"):
    os.environ.setdefault("GDK_BACKEND", "x11")

import base64
import hmac
import http.server
import json
import math
import mimetypes
import re
import secrets
import shutil
import signal
import socketserver
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings
from datetime import datetime

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, WebKit2  # noqa: E402
# Nome fixo das janelas (WM_CLASS "tupi-assistant"): o executor usa para a IA nunca clicar nem digitar nelas
GLib.set_prgname("tupi-assistant")
Gdk.set_program_class("tupi-assistant")

import cairo  # noqa: E402

# Agente (ferramentas + executor + política): pacote tupi_agent (PYTHONPATH do wrapper)
try:
    from tupi_agent.tools import (AGENT_PROMPT, COMPUTER_TOOLS, EXEC_SOCKET, ORCH_SCHEMAS, ORCH_TOOLS,  # noqa: E402
                                  TOOL_SCHEMAS, exec_op, paje_op, perm_op, request as sock_request,
                                  result_for_model)
    from tupi_agent import conexoes, souls  # noqa: E402
    AGENT_IMPORT = True
except ImportError:
    AGENT_IMPORT, AGENT_PROMPT, TOOL_SCHEMAS, EXEC_SOCKET = False, "", [], ""
    ORCH_SCHEMAS, ORCH_TOOLS, COMPUTER_TOOLS = [], set(), set()
    conexoes = None

    def paje_op(*_a, **_k):
        return {"error": "o motor do Pajé não está instalado"}

MCP_SOCKET = os.environ.get("TUPI_MCP_SOCKET", "/run/tupi-agents/mcp.sock")
LOOP_CFG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "tupios-ia", "loop.json")
LOOP_STATUS = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "tupios-ia",
                           "loop-status.json")
SUGGESTIONS = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "tupios-ia",
                           "sugestoes.json")


def agent_available():
    return AGENT_IMPORT and os.path.exists(EXEC_SOCKET)


APP_ID = "dev.tupios.Assistant"
ROUTER = os.environ.get("TUPI_AI_URL", "http://127.0.0.1:8080").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.environ.get("TUPI_ASSISTANT_UI", os.path.join(HERE, "ui"))
WHISPER_MODEL = os.environ.get("TUPI_WHISPER_MODEL", "")
WHISPER_LANG = os.environ.get("TUPI_WHISPER_LANG", "pt")
CONFIG_GUI = os.environ.get("TUPI_AI_CONFIG_GUI", "tupi-ai-config-gui")

XDG_DATA = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
XDG_CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
DATA_DIR = os.path.join(XDG_DATA, "tupios-ia")
CONV_DIR = os.path.join(DATA_DIR, "conversas")
ATT_DIR = os.path.join(DATA_DIR, "anexos")
STATE_FILE = os.path.join(XDG_CONFIG, "tupios-ia", "estado.json")


def pictures_dir():
    d = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or os.path.expanduser("~/Imagens")
    return os.path.join(d, "Capturas TUPIOS")


MAX_UPLOAD = 40 * 1024 * 1024      # tamanho máximo de um anexo
MAX_PDF_TEXT = 150_000             # caracteres de texto de PDF enviados a modelos sem leitura nativa
IMG_MAX_SIDE = 1568                # lado máximo das imagens enviadas (recomendado para visão)
CHAT_TIMEOUT = 330

SYSTEM_PROMPT = (
    "Você é o Pajé, a IA integrada ao sistema operacional TUPIOS (baseado em NixOS). "
    "Responda em português do Brasil, de forma clara e objetiva, usando Markdown quando ajudar. "
    "Quando o usuário enviar imagens, prints de tela, PDFs ou transcrições de áudio, use esse conteúdo na resposta."
)

# Configuração fixa para testes automatizados (opcional)
FIXED_PORT = int(os.environ.get("TUPI_ASSISTANT_PORT", "0"))
TOKEN = os.environ.get("TUPI_ASSISTANT_TOKEN") or secrets.token_urlsafe(24)


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


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


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ----------------------------------------------------------------- router
def router_request(method, path, payload=None, timeout=20):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(ROUTER + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            err = json.loads(raw).get("error", raw)
            msg = err.get("message", err) if isinstance(err, dict) else err
        except ValueError:
            msg = raw
        raise ApiError(e.code, str(msg))
    except urllib.error.URLError as e:
        raise ApiError(502, f"O TUPIOS AI Router não respondeu ({e.reason}). "
                            "Verifique: systemctl status tupi-ai-router")


def router_status():
    try:
        return router_request("GET", "/health", timeout=8)
    except ApiError as e:
        return {"status": "erro", "configured": False, "hint": e.message}


def native_pdf(status):
    """O provedor lê PDF diretamente? (Claude e OpenAI sim; os demais recebem o texto extraído)."""
    provider = status.get("provider")
    base = status.get("base_url") or ""
    return provider == "anthropic" or (provider == "openai" and base.startswith("https://api.openai.com"))


# ---------------------------------------------------------------- anexos
def att_dir(att_id):
    if not re.fullmatch(r"[0-9a-f]{32}", att_id or ""):
        raise ApiError(400, "anexo inválido")
    return os.path.join(ATT_DIR, att_id)


def load_attachment(att_id):
    meta = load_json(os.path.join(att_dir(att_id), "meta.json"), None)
    if not meta:
        raise ApiError(404, "anexo não encontrado")
    return meta


def public_attachment(meta):
    out = {k: meta.get(k) for k in ("id", "kind", "name", "mime", "pages", "truncated", "chars", "seconds")}
    if meta.get("kind") == "audio":
        out["transcript"] = meta.get("text", "")
    if meta.get("kind") == "pdf":
        out["excerpt"] = (meta.get("text") or "")[:300]
    if meta.get("kind") == "image":
        out["thumb"] = f"/api/attachments/{meta['id']}/image"
    return out


def run(cmd, timeout, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)


def guess_kind(name, mime):
    mime = (mime or mimetypes.guess_type(name)[0] or "").lower()
    ext = os.path.splitext(name)[1].lower()
    if mime.startswith("image/"):
        return "image", mime
    if mime == "application/pdf" or ext == ".pdf":
        return "pdf", "application/pdf"
    if mime.startswith("audio/") or mime in ("video/webm", "video/ogg") or ext in (
            ".mp3", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".flac", ".webm", ".amr", ".3gp"):
        return "audio", mime or "audio/unknown"
    raise ApiError(415, f"Tipo de arquivo não suportado: {name}. Envie imagem, PDF ou áudio.")


def prepare_image(src, dst):
    """Redimensiona (lado máximo IMG_MAX_SIDE) e salva em JPEG ou PNG."""
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(src)
    except GLib.Error as e:
        raise ApiError(415, f"Não foi possível abrir a imagem: {e.message}")
    w, h = pb.get_width(), pb.get_height()
    scale = min(1.0, IMG_MAX_SIDE / max(w, h))
    if scale < 1.0:
        pb = pb.scale_simple(max(1, int(w * scale)), max(1, int(h * scale)), GdkPixbuf.InterpType.BILINEAR)
    if pb.get_has_alpha():
        pb.savev(dst + ".png", "png", [], [])
        return dst + ".png", "image/png", pb.get_width(), pb.get_height()
    pb.savev(dst + ".jpg", "jpeg", ["quality"], ["88"])
    return dst + ".jpg", "image/jpeg", pb.get_width(), pb.get_height()


def whisper_cli():
    """Variante AVX2 (rápida) se a CPU suportar; senão a básica (qualquer x86-64)."""
    fast, basic = os.environ.get("TUPI_WHISPER_CLI_AVX2"), os.environ.get("TUPI_WHISPER_CLI_BASIC")
    try:
        with open("/proc/cpuinfo") as f:
            flags = set(re.search(r"^flags\s*:\s*(.*)$", f.read(), re.M).group(1).split())
    except (OSError, AttributeError):
        flags = set()
    if fast and os.path.exists(fast) and {"avx2", "fma", "f16c"} <= flags:
        return fast
    if basic and os.path.exists(basic):
        return basic
    return shutil.which("whisper-cli")


def transcribe(src):
    """Transcreve áudio localmente com whisper.cpp (nada sai da máquina)."""
    cli = whisper_cli()
    if not WHISPER_MODEL or not os.path.exists(WHISPER_MODEL) or not cli:
        raise ApiError(501, "Transcrição de áudio indisponível (whisper.cpp não instalado).")
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "audio16k.wav")
        # 1 s de silêncio no fim melhora a transcrição de áudios curtos
        r = run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-af", "apad=pad_dur=1",
                 "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav], timeout=180)
        if r.returncode != 0 or not os.path.exists(wav):
            raise ApiError(415, "Não foi possível ler o áudio: " + (r.stderr.strip()[-300:] or "formato inválido"))
        seconds = max(0.0, os.path.getsize(wav) / 32000.0 - 1.0)
        threads = str(max(1, min(8, (os.cpu_count() or 2))))
        r = run([cli, "-m", WHISPER_MODEL, "-l", WHISPER_LANG, "-t", threads, "-bs", "5",
                 "-nt", "-np", "-f", wav], timeout=900)
        if r.returncode != 0:
            why = r.stderr.strip()[-300:] or f"código de saída {r.returncode}"
            raise ApiError(500, "Falha na transcrição: " + why)
        text = " ".join(line.strip() for line in r.stdout.splitlines() if line.strip())
        return text.strip(), seconds


def create_attachment(name, mime, raw):
    if len(raw) > MAX_UPLOAD:
        raise ApiError(413, f"Arquivo muito grande (máximo {MAX_UPLOAD // (1024 * 1024)} MB).")
    kind, mime = guess_kind(name, mime)
    att_id = uuid.uuid4().hex
    d = os.path.join(ATT_DIR, att_id)
    os.makedirs(d, exist_ok=True)
    ext = os.path.splitext(name)[1].lower() or mimetypes.guess_extension(mime) or ""
    original = os.path.join(d, "original" + ext)
    with open(original, "wb") as f:
        f.write(raw)
    meta = {"id": att_id, "kind": kind, "name": name, "mime": mime, "file": os.path.basename(original),
            "created": now_iso()}
    try:
        if kind == "image":
            path, mime2, w, h = prepare_image(original, os.path.join(d, "image"))
            meta.update(image=os.path.basename(path), image_mime=mime2, width=w, height=h)
        elif kind == "pdf":
            r = run(["pdftotext", "-enc", "UTF-8", original, "-"], timeout=120)
            text = r.stdout if r.returncode == 0 else ""
            info = run(["pdfinfo", original], timeout=30)
            m = re.search(r"^Pages:\s+(\d+)", info.stdout or "", re.M)
            meta["pages"] = int(m.group(1)) if m else None
            meta["chars"] = len(text)
            meta["truncated"] = len(text) > MAX_PDF_TEXT
            meta["text"] = text[:MAX_PDF_TEXT]
        else:
            text, seconds = transcribe(original)
            meta.update(text=text, seconds=round(seconds, 1))
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    save_json(os.path.join(d, "meta.json"), meta)
    return meta


def b64_file(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def api_content(msg, status):
    """Mensagem do usuário (texto + anexos) -> conteúdo no formato OpenAI."""
    atts = [load_attachment(a) for a in msg.get("attachments") or []]
    if not atts:
        return msg.get("text") or ""
    parts = []
    for a in atts:
        d = att_dir(a["id"])
        if a["kind"] == "image":
            parts.append({"type": "image_url", "image_url": {
                "url": f"data:{a['image_mime']};base64," + b64_file(os.path.join(d, a["image"]))}})
        elif a["kind"] == "pdf":
            if native_pdf(status):
                parts.append({"type": "file", "file": {
                    "filename": a["name"],
                    "file_data": "data:application/pdf;base64," + b64_file(os.path.join(d, a["file"]))}})
            elif a.get("text", "").strip():
                note = (f" — texto truncado: {MAX_PDF_TEXT} de {a.get('chars')} caracteres"
                        if a.get("truncated") else "")
                parts.append({"type": "text", "text": f"[Conteúdo do PDF \"{a['name']}\"{note}]\n{a['text']}"})
            else:
                parts.append({"type": "text", "text": f"[O PDF \"{a['name']}\" não tem texto extraível "
                                                      "(provavelmente é escaneado) e este provedor não lê PDF "
                                                      "diretamente. Avise o usuário.]"})
        elif a["kind"] == "audio":
            parts.append({"type": "text", "text": f"[Transcrição do áudio \"{a['name']}\"]\n{a.get('text', '')}"})
    if msg.get("text"):
        parts.append({"type": "text", "text": msg["text"]})
    return parts


# -------------------------------------------------------------- conversas
def conv_path(cid):
    if not re.fullmatch(r"[0-9a-f]{32}", cid or ""):
        raise ApiError(400, "conversa inválida")
    return os.path.join(CONV_DIR, cid + ".json")


def load_conv(cid):
    c = load_json(conv_path(cid), None)
    if not c:
        raise ApiError(404, "conversa não encontrada")
    return c


def list_convs():
    out = []
    if os.path.isdir(CONV_DIR):
        for fn in os.listdir(CONV_DIR):
            if fn.endswith(".json"):
                c = load_json(os.path.join(CONV_DIR, fn), None)
                if c:
                    out.append({"id": c["id"], "title": c.get("title") or "Nova conversa",
                                "updated": c.get("updated"), "count": len(c.get("messages", []))})
    out.sort(key=lambda c: c.get("updated") or "", reverse=True)
    return out


def new_conv(model=None):
    cid = uuid.uuid4().hex
    c = {"id": cid, "title": "Nova conversa", "created": now_iso(), "updated": now_iso(),
         "model": model, "messages": []}
    save_json(conv_path(cid), c)
    return c


def public_conv(c):
    msgs = []
    for m in c.get("messages", []):
        pm = {k: m.get(k) for k in ("id", "role", "text", "time", "model", "error", "auto", "action", "name")}
        if m.get("attachments"):
            pm["attachments"] = []
            for a in m["attachments"]:
                try:
                    pm["attachments"].append(public_attachment(load_attachment(a)))
                except ApiError:
                    pm["attachments"].append({"id": a, "kind": "missing", "name": "(anexo removido)"})
        msgs.append(pm)
    job = JOBS.get(c["id"])
    return {"id": c["id"], "title": c.get("title"), "model": c.get("model"), "messages": msgs,
            "job": {"running": bool(job and job.thread.is_alive()), "step": job.step if job else ""}}


def make_title(text, atts):
    t = re.sub(r"\s+", " ", text or "").strip()
    if not t and atts:
        t = atts[0].get("name") or "Anexo"
    return (t[:48] + "…") if len(t) > 48 else (t or "Nova conversa")


# ---------------------------------------------------------------- agente
MAX_STEPS = 15
MAX_STEPS_COMPUTER = int(os.environ.get("TUPI_MAX_STEPS_COMPUTER", "40"))
CONV_LOCK = threading.RLock()


class Job:
    def __init__(self, cid):
        self.cid = cid
        self.cancel = threading.Event()
        self.step = "pensando…"
        self.thread = None
        self.cmdline = None      # tupi-assistant --send: imprime a resposta no terminal ao terminar
        self.final = ""


JOBS = {}


def update_conv(cid, fn):
    """Lê, altera e grava a conversa sob trava (o laço do agente e a interface usam ao mesmo tempo)."""
    with CONV_LOCK:
        c = load_conv(cid)
        out = fn(c)
        c["updated"] = now_iso()
        save_json(conv_path(cid), c)
        return out


def paje_system():
    """Constituição fixa + alma do Pajé (editável) + a equipe de subagentes de agora."""
    try:
        team = (paje_op("agents/list", 5) or {}).get("agentes")
        return souls.paje_system_prompt(team)
    except Exception:
        return AGENT_PROMPT


def build_history(c, status, tools_on):
    system = paje_system() if tools_on else SYSTEM_PROMPT
    history = [{"role": "system", "content": system}]
    # prints automáticos da tela (use_computer): só os 2 últimos vão como imagem; os antigos viram texto
    autos = [m["id"] for m in c["messages"] if m["role"] == "user" and m.get("auto") and m.get("attachments")]
    old_autos = set(autos[:-2])
    for m in c["messages"]:
        if m.get("error"):
            continue
        if m["role"] == "user" and m["id"] in old_autos:
            history.append({"role": "user", "content": "(print antigo da tela, removido para economizar; veja o "
                                                       "mais recente)"})
        elif m["role"] == "user":
            history.append({"role": "user", "content": api_content(m, status)})
        elif m["role"] == "assistant":
            if m.get("api") and tools_on:
                history.append(m["api"])            # exatamente como veio (só-acrescentar)
            elif m.get("text"):
                history.append({"role": "assistant", "content": m["text"]})
        elif m["role"] == "tool" and tools_on:
            h = {"role": "tool", "tool_call_id": m.get("tool_call_id"), "content": m.get("content") or ""}
            if m.get("is_error"):
                h["tupi_is_error"] = True
            history.append(h)
    return history


def tools_unsupported(err):
    low = err.lower()
    return any(w in low for w in ("tool", "function", "does not support", "não suport")) and "401" not in low


def close_open_calls(c, reason):
    """Toda tool_call sem resposta recebe um resultado (a API exige o par tool_use/tool_result)."""
    answered = {m.get("tool_call_id") for m in c["messages"] if m["role"] == "tool"}
    for m in c["messages"]:
        for tc in (m.get("api") or {}).get("tool_calls") or []:
            if tc.get("id") not in answered:
                c["messages"].append({"id": uuid.uuid4().hex, "role": "tool", "tool_call_id": tc["id"],
                                      "name": (tc.get("function") or {}).get("name"), "content": reason,
                                      "is_error": True, "time": now_iso(),
                                      "action": {"status": "cancelled", "summary": "Cancelado", "reason": reason}})


def compact_tasks(ts):
    return [{"id": t.get("id"), "agente": t.get("agente_nome"), "titulo": t.get("titulo"), "status": t.get("status"),
             "aguardando_aprovacao": t.get("aguardando_aprovacao", False),
             "relatorio": (t.get("resultado") or "")[:6000] or None, "erro": t.get("erro"),
             "passos": len(t.get("passos") or []), "tokens": t.get("tokens")} for t in ts or []]


def orch_call(job, cid, name, args):
    """Ferramentas de orquestração do Pajé (motor tupi-paje, sem privilégio). -> resposta no formato do executor.
    As ações de SISTEMA dos subagentes continuam passando pelo executor/Permission Manager, uma a uma."""
    summary = {"listar_agentes": "Ver a equipe de subagentes", "ver_tarefas": "Ver os trabalhos",
               "aguardar_tarefas": "Aguardar os relatórios dos subagentes"}.get(name, name)
    if name == "listar_agentes":
        r = paje_op("agents/list", 10)
        out = r if r.get("error") else {"agentes": [{k: a.get(k) for k in ("id", "nome", "papel", "ferramentas",
                                                                             "conexao", "modelo", "estado")}
                                                    for a in r.get("agentes", [])]}
    elif name == "criar_subagente":
        summary = f"Criar o subagente {args.get('nome') or ''}".strip()
        r = paje_op("agents/create", 15, nome=args.get("nome"), papel=args.get("papel"), alma=args.get("alma"),
                    ferramentas=args.get("ferramentas"), conexao=args.get("conexao"), modelo=args.get("modelo"),
                    criado_por="paje")
        out = r if r.get("error") else {"criado": {k: r["agente"].get(k) for k in ("id", "nome", "papel", "ferramentas",
                                                                                   "conexao", "modelo", "arquivo")}}
    elif name == "delegar_tarefas":
        tarefas = args.get("tarefas") if isinstance(args.get("tarefas"), list) else []
        summary = f"Delegar {len(tarefas)} trabalho(s) em paralelo"
        r = paje_op("tasks/create_many", 20, tarefas=tarefas, origem="paje", conversa=cid)
        out = r
        if not r.get("error") and not r.get("ok"):
            r = {"error": "; ".join(t.get("erro", "") for t in r.get("trabalhos", [])) or "nenhum trabalho criado"}
    elif name == "aguardar_tarefas":
        end = time.time() + max(5, min(int(args.get("segundos") or 300), 900))
        ids = args.get("ids") if isinstance(args.get("ids"), list) and args.get("ids") else None
        while True:
            job.step = "esperando os subagentes…"
            r = paje_op("tasks/wait", 40, ids=ids, conversa=cid, segundos=max(0, min(10, end - time.time())))
            if r.get("error") or r.get("terminados") or time.time() >= end or job.cancel.is_set():
                break
        out = r if r.get("error") else {"terminados": r.get("terminados"), "trabalhos": compact_tasks(r.get("trabalhos"))}
    elif name == "ver_tarefas":
        ids = args.get("ids") if isinstance(args.get("ids"), list) and args.get("ids") else None
        r = paje_op("tasks/list", 10, ids=ids, conversa=None if ids else cid)
        out = r if r.get("error") else {"trabalhos": compact_tasks(r.get("trabalhos"))}
    elif name == "cancelar_tarefa":
        summary = f"Cancelar o trabalho {args.get('id')}"
        r = paje_op("tasks/cancel", 10, id=args.get("id"))
        out = r if r.get("error") else {"cancelado": compact_tasks([r.get("trabalho") or {}])}
    else:
        r = out = {"error": f"ferramenta de orquestração desconhecida: {name}"}
    base = {"onde": "Pajé", "categories": [], "decision": "allow", "approved_by": "Pajé", "summary": summary}
    if r.get("error"):
        return dict(base, status="error", exit_code=1, result={"error": r["error"]}, reason=r["error"])
    return dict(base, status="ok", exit_code=0, result=out)


def run_agent(job):
    cid = job.cid
    tools_on = agent_available()
    paje_up = tools_on and bool(paje_op("status", 3).get("ok"))
    soul = souls.load_paje_soul() if tools_on else {}
    max_steps, step = MAX_STEPS, 0
    try:
        while True:
            step += 1
            if step > max_steps:
                update_conv(cid, lambda c: c["messages"].append(
                    {"id": uuid.uuid4().hex, "role": "assistant", "error": True, "time": now_iso(),
                     "text": f"Parei depois de {max_steps} passos para não entrar em laço. Peça para continuar se "
                             "quiser."}))
                break
            if job.cancel.is_set():
                break
            job.step = "pensando…"
            c = load_conv(cid)
            status = router_status()
            payload = {"messages": build_history(c, status, tools_on)}
            if tools_on:
                payload["tools"] = TOOL_SCHEMAS + (ORCH_SCHEMAS if paje_up else [])
                payload["tool_choice"] = "auto"
            if c.get("model"):
                payload["model"] = c["model"]
            elif soul.get("modelo"):
                payload["model"] = soul["modelo"]
            if soul.get("conexao") and soul["conexao"] != "principal" and not c.get("model"):
                payload["tupi_conexao"] = soul["conexao"]
            try:
                data = router_request("POST", "/v1/chat/completions", payload, timeout=CHAT_TIMEOUT)
            except ApiError as e:
                if tools_on and tools_unsupported(e.message):
                    tools_on = False   # modelo sem suporte a ferramentas: segue só conversando
                    continue
                update_conv(cid, lambda c: c["messages"].append(
                    {"id": uuid.uuid4().hex, "role": "assistant", "text": e.message, "error": True,
                     "time": now_iso()}))
                job.final = e.message
                return
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            text = msg.get("content") or ""
            if isinstance(text, list):
                text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
            calls = (msg.get("tool_calls") or []) if tools_on else []
            api = {k: msg[k] for k in ("role", "content", "tool_calls", "tupi_raw") if k in msg}
            api["role"] = "assistant"
            update_conv(cid, lambda c: c["messages"].append(
                {"id": uuid.uuid4().hex, "role": "assistant", "text": text.strip(), "api": api,
                 "model": data.get("model") or c.get("model"), "time": now_iso()}))
            if not calls:
                job.final = text.strip()
                return
            screenshots = []
            for tc in calls:
                f = tc.get("function") or {}
                name = f.get("name") or ""
                try:
                    args = json.loads(f.get("arguments") or "{}")
                except ValueError:
                    args = {}
                mid = uuid.uuid4().hex
                if job.cancel.is_set():
                    break
                job.step = f"{name}…"
                update_conv(cid, lambda c: c["messages"].append(
                    {"id": mid, "role": "tool", "tool_call_id": tc.get("id"), "name": name, "content": "",
                     "time": now_iso(), "action": {"status": "running", "tool": name, "args": args,
                                                   "summary": f"{name}"}}))
                if name in ORCH_TOOLS:
                    r = orch_call(job, cid, name, args)
                else:
                    r = exec_op("run", tool=name, args=args, session=cid, display=os.environ.get("DISPLAY"),
                                xauthority=os.environ.get("XAUTHORITY"), desktop=os.environ.get("XDG_CURRENT_DESKTOP"))
                content, is_err = result_for_model(r)
                res = r.get("result") if isinstance(r.get("result"), dict) else {}
                output = res.get("output") if "output" in res else json.dumps(res, ensure_ascii=False, indent=1) if res else ""
                action = {"status": r.get("status") or "error", "tool": name, "args": args,
                          "summary": r.get("summary") or name, "decision": r.get("decision"),
                          "categories": r.get("categories", []), "risk": r.get("risk"),
                          "reason": r.get("reason") or r.get("error"), "exit_code": r.get("exit_code"),
                          "approved_by": r.get("approved_by"), "action_id": r.get("action_id"),
                          "can_undo": bool(r.get("can_undo")), "output": (output or "")[:4000],
                          "destructive": r.get("destructive", False), "onde": r.get("onde")}

                def finish(c, mid=mid, content=content, is_err=is_err, action=action):
                    for m in c["messages"]:
                        if m["id"] == mid:
                            m.update(content=content, is_error=is_err, action=action)
                update_conv(cid, finish)
                if name in ("take_screenshot", "use_computer") and r.get("status") == "ok" and res.get("arquivo"):
                    screenshots.append(res["arquivo"])
                if name in COMPUTER_TOOLS:
                    max_steps = MAX_STEPS_COMPUTER   # mouse/teclado: muitos passos curtos (print -> ação -> print)
            if job.cancel.is_set():
                break
            for shot in screenshots:
                try:
                    with open(shot, "rb") as fh:
                        meta = create_attachment(os.path.basename(shot), "image/png", fh.read())
                    update_conv(cid, lambda c: c["messages"].append(
                        {"id": uuid.uuid4().hex, "role": "user", "auto": True, "attachments": [meta["id"]],
                         "text": "(print da tela tirado pelo Pajé a seu pedido)", "time": now_iso()}))
                except (OSError, ApiError):
                    pass
        if job.cancel.is_set():
            def cancelled(c):
                close_open_calls(c, "Cancelado pelo usuário (PARAR IA).")
                c["messages"].append({"id": uuid.uuid4().hex, "role": "assistant", "error": True,
                                      "time": now_iso(), "text": "IA parada pelo usuário. Nada mais foi executado."})
            update_conv(cid, cancelled)
            job.final = "IA parada pelo usuário."
    except Exception as e:  # o laço nunca deve derrubar o app
        update_conv(cid, lambda c: (close_open_calls(c, f"erro interno: {e}"), c["messages"].append(
            {"id": uuid.uuid4().hex, "role": "assistant", "error": True, "time": now_iso(),
             "text": f"Erro interno do agente: {e}"})))
        job.final = f"Erro interno: {e}"
    finally:
        job.step = ""
        if job.cmdline is not None:
            GLib.idle_add(finish_cmdline, job)


def finish_cmdline(job):
    """Resposta do --send: as ações (com a decisão da política) e o texto final."""
    lines = []
    try:
        msgs = load_conv(job.cid)["messages"]
        last_user = max((i for i, m in enumerate(msgs) if m["role"] == "user" and not m.get("auto")), default=-1)
        for m in msgs[last_user + 1:]:
            a = m.get("action")
            if m["role"] == "tool" and a:
                extra = a.get("reason") or (f"código {a['exit_code']}" if a.get("exit_code") is not None else "")
                try:   # mensagem do resultado (ex.: "já estão instalados no PD", erro do executor)
                    res = json.loads(a.get("output") or "{}")
                    msg = res.get("mensagem") or res.get("error") if isinstance(res, dict) else None
                    if msg and msg not in extra:
                        extra = (extra + " — " if extra else "") + str(msg)[:300]
                except ValueError:
                    pass
                lines.append(f"[ação] {a.get('tool')}: {a.get('status')} (política: {a.get('decision') or '-'}"
                             f"{', aprovado: ' + a['approved_by'] if a.get('approved_by') else ''})"
                             f" — {a.get('summary') or ''}{' — ' + extra if extra else ''}")
    except (ApiError, KeyError):
        pass
    lines.append(job.final or "(sem resposta)")
    try:
        job.cmdline.print_literal("\n".join(lines) + "\n")
    except AttributeError:
        pass
    job.cmdline = None
    return False


def send_message(cid, text, attachments, model, retry=False, cmdline=None):
    if cid in JOBS and JOBS[cid].thread.is_alive():
        raise ApiError(409, "A IA ainda está trabalhando nesta conversa. Aguarde ou clique em PARAR.")
    atts = [load_attachment(a) for a in attachments]
    if not (text or "").strip() and not atts:
        raise ApiError(400, "Escreva uma mensagem ou anexe um arquivo.")

    def add(c):
        if retry:
            # "Tentar de novo": tira o erro e a última pergunta (que é reenviada)
            while c["messages"] and (c["messages"][-1].get("error") or c["messages"][-1]["role"] == "user"):
                c["messages"].pop()
        else:
            c["messages"] = [m for m in c["messages"] if not (m.get("error") and m["role"] == "assistant")]
        close_open_calls(c, "Interrompido.")
        c["messages"].append({"id": uuid.uuid4().hex, "role": "user", "text": text or "",
                              "attachments": [a["id"] for a in atts], "time": now_iso()})
        if c.get("title") in (None, "", "Nova conversa"):
            c["title"] = make_title(text, atts)
        if model:
            c["model"] = model
    update_conv(cid, add)
    job = Job(cid)
    job.cmdline = cmdline
    job.thread = threading.Thread(target=run_agent, args=(job,), daemon=True)
    JOBS[cid] = job
    job.thread.start()
    return public_conv(load_conv(cid))


def cancel_jobs():
    for job in list(JOBS.values()):
        job.cancel.set()

# ------------------------------------------------------- gravação de voz
class Recorder:
    def __init__(self):
        self.proc, self.path, self.lock = None, None, threading.Lock()

    def start(self):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return
            fd, self.path = tempfile.mkstemp(prefix="tupi-voz-", suffix=".wav")
            os.close(fd)
            if shutil.which("pw-record"):
                cmd = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", self.path]
            elif shutil.which("arecord"):
                cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1", "-t", "wav", self.path]
            else:
                raise ApiError(501, "Nenhum gravador de áudio disponível (pw-record/arecord).")
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            time.sleep(0.4)
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() or b"").decode("utf-8", "replace").strip()
                self.proc = None
                raise ApiError(500, "Não foi possível acessar o microfone. " + err[-200:])

    def stop(self):
        with self.lock:
            proc, path = self.proc, self.path
            self.proc = None
        if not proc:
            raise ApiError(400, "Nenhuma gravação em andamento.")
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        try:
            if os.path.getsize(path) < 8000:   # ~0,25 s de áudio
                raise ApiError(400, "Gravação muito curta ou microfone sem som.")
            text, seconds = transcribe(path)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if not text:
            raise ApiError(422, "Não entendi o áudio. Tente falar mais perto do microfone.")
        return {"text": text, "seconds": round(seconds, 1)}


# ---------------------------------------------------- servidor HTTP local
STATIC = {"/": "index.html", "/index.html": "index.html", "/style.css": "style.css", "/app.js": "app.js"}


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "TUPIOS-IA/0.1"
    app = None  # preenchido em Assistant.start_server

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, body, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _authorized(self, query):
        token = self.headers.get("X-Tupi-Token") or (query.get("token") or [""])[0]
        return hmac.compare_digest(token, TOKEN)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD * 1.4 + 65536:
            raise ApiError(413, "Requisição muito grande.")
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "JSON inválido")

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        try:
            if method == "GET" and path in STATIC:
                ctype = {"html": "text/html", "css": "text/css", "js": "application/javascript"}[
                    STATIC[path].rsplit(".", 1)[1]] + "; charset=utf-8"
                with open(os.path.join(UI_DIR, STATIC[path]), "rb") as f:
                    return self._send(200, f.read(), ctype)
            if not path.startswith("/api/"):
                raise ApiError(404, "não encontrado")
            if not self._authorized(query):
                raise ApiError(403, "token inválido")
            return self._api(method, path, query)
        except ApiError as e:
            self._json(e.status, {"error": e.message})
        except Exception as e:  # erro inesperado: mostra na interface em vez de derrubar o servidor
            self._json(500, {"error": f"erro interno: {e}"})

    def _api(self, method, path, query):
        app = self.app
        parts = path.strip("/").split("/")[1:]   # sem o "api"
        if parts == ["status"] and method == "GET":
            st = router_status()
            st["whisper"] = bool(WHISPER_MODEL and os.path.exists(WHISPER_MODEL) and whisper_cli())
            st["recorder"] = bool(shutil.which("pw-record") or shutil.which("arecord"))
            st["native_pdf"] = native_pdf(st)
            return self._json(200, st)
        if parts == ["models"] and method == "GET":
            try:
                data = router_request("GET", "/v1/models", timeout=30)
                return self._json(200, {"models": [m.get("id") for m in data.get("data", []) if m.get("id")]})
            except ApiError as e:
                return self._json(200, {"models": [], "error": e.message})
        if parts == ["conversations"]:
            if method == "GET":
                return self._json(200, {"conversations": list_convs()})
            if method == "POST":
                b = self._body()
                return self._json(200, public_conv(new_conv(b.get("model"))))
        if len(parts) >= 2 and parts[0] == "conversations":
            cid = parts[1]
            if len(parts) == 2 and method == "GET":
                return self._json(200, public_conv(load_conv(cid)))
            if len(parts) == 2 and method == "DELETE":
                try:
                    os.unlink(conv_path(cid))
                except FileNotFoundError:
                    pass
                return self._json(200, {"ok": True})
            if len(parts) == 3 and parts[2] == "messages" and method == "POST":
                b = self._body()
                return self._json(200, send_message(cid, b.get("text", ""), b.get("attachments") or [],
                                                    b.get("model"), bool(b.get("retry"))))
            if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
                job = JOBS.get(cid)
                if job and job.thread.is_alive():
                    job.cancel.set()
                return self._json(200, {"ok": True})
            if len(parts) == 3 and parts[2] == "model" and method == "POST":
                c = load_conv(cid)
                c["model"] = self._body().get("model") or None
                save_json(conv_path(cid), c)
                return self._json(200, {"ok": True})
        if parts == ["attachments"] and method == "POST":
            b = self._body()
            try:
                raw = base64.b64decode(b.get("data") or "", validate=True)
            except ValueError:
                raise ApiError(400, "arquivo inválido")
            meta = create_attachment(b.get("name") or "arquivo", b.get("mime"), raw)
            return self._json(200, public_attachment(meta))
        if len(parts) == 3 and parts[0] == "attachments" and parts[2] == "image" and method == "GET":
            meta = load_attachment(parts[1])
            if meta.get("kind") != "image":
                raise ApiError(404, "não é imagem")
            with open(os.path.join(att_dir(meta["id"]), meta["image"]), "rb") as f:
                return self._send(200, f.read(), meta["image_mime"])
        if parts == ["record", "start"] and method == "POST":
            app.recorder.start()
            return self._json(200, {"recording": True})
        if parts == ["record", "stop"] and method == "POST":
            return self._json(200, app.recorder.stop())
        if parts == ["screenshot"] and method == "POST":
            mode = self._body().get("mode", "full")
            path = app.take_screenshot(mode)
            with open(path, "rb") as f:
                raw = f.read()
            meta = create_attachment(os.path.basename(path), "image/png", raw)
            return self._json(200, public_attachment(meta))
        if parts == ["window"] and method == "POST":
            action = self._body().get("action")
            app.window_action(action)
            return self._json(200, {"ok": True})
        if parts and parts[0] in ("agent", "policy", "memory", "loop", "suggestions", "st", "paje"):
            return self._json(200, agent_api(app, method, parts, self._body() if method == "POST" else {}))
        raise ApiError(404, "rota desconhecida")


def mcp(method, **params):
    try:
        return sock_request(MCP_SOCKET, {"method": method, "agent_id": "tupi-app", "params": params}, 10)
    except (OSError, ValueError, NameError) as e:
        return {"error": f"memória indisponível ({e})"}


LOOP_DEFAULTS = {"enabled": True, "interval_min": 15, "daily_tokens": 20000, "silent": False,
                 "sources": {"apps": True, "terminal": True, "files": True, "browser": False}}


def apply_suggestion(app, s):
    """Executa exatamente a ação/arquivo que o usuário viu (passa pelo executor e pela política)."""
    def work():
        results = []
        if isinstance(s.get("arquivo"), dict) and s["arquivo"].get("path"):
            results.append(exec_op("run", tool="write_file", session="sugestao-" + s["id"],
                                   args={"path": s["arquivo"]["path"], "content": s["arquivo"].get("content", "")}))
        acao = s.get("acao")
        if isinstance(acao, dict) and acao.get("tool"):
            results.append(exec_op("run", tool=acao["tool"], args=acao.get("args") or {},
                                   session="sugestao-" + s["id"], display=os.environ.get("DISPLAY"),
                                   xauthority=os.environ.get("XAUTHORITY")))
        sugs = load_json(SUGGESTIONS, [])
        for x in sugs:
            if x.get("id") == s["id"]:
                ok = results and all(r.get("status") == "ok" for r in results)
                x["status"] = "feita" if ok else "falhou"
                x["resultado"] = [{"status": r.get("status"), "summary": r.get("summary"),
                                   "reason": r.get("reason") or r.get("error")} for r in results]
        save_json(SUGGESTIONS, sugs)
    threading.Thread(target=work, daemon=True).start()


ST_PANEL = {"ultima": None}   # última promoção pedida pelo painel "Mudanças em ensaio"


def st_promote_from_panel():
    """Botão "Promover para o PD": passa pelo executor e pela política como qualquer ação (sempre pede
    aprovação, com o diff, no cartão de aprovação)."""
    ST_PANEL["ultima"] = {"status": "aguardando", "quando": time.time()}
    r = exec_op("run", tool="st_promote", args={}, session="painel-st", display=os.environ.get("DISPLAY"),
                xauthority=os.environ.get("XAUTHORITY"))
    ST_PANEL["ultima"] = {"status": r.get("status"), "quando": time.time(), "summary": r.get("summary"),
                          "reason": r.get("reason") or r.get("error"), "action_id": r.get("action_id"),
                          "can_undo": bool(r.get("can_undo")),
                          "result": r.get("result") if isinstance(r.get("result"), dict) else None}


# Conexões de IA (as extras e o provedor próprio de cada subagente): módulo compartilhado com o terminal (paje)
PRESETS = conexoes.PRESETS if conexoes else []


def cfg_call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except conexoes.ConfigError as e:
        raise ApiError(e.code, e.msg)


def preset_params(body):
    return cfg_call(conexoes.preset_params, body)


def edit_ai_config(fn):
    return cfg_call(conexoes.edit_ai_config, fn)


def paje_api(method, parts, body):
    """/api/paje/...: subagentes, trabalhos, harness e conexões (o motor é o serviço tupi-paje)."""
    sub = parts[1] if len(parts) > 1 else "status"
    arg = parts[2] if len(parts) > 2 else None
    act = parts[3] if len(parts) > 3 else None

    def ok(r):
        if r.get("error"):
            raise ApiError(400, r["error"])
        return r
    if sub == "status":
        return paje_op("status", 5)
    if sub == "agentes":
        if arg is None:
            if method == "POST":
                params = {k: body.get(k) for k in (
                    "nome", "papel", "alma", "ferramentas", "conexao", "modelo", "orcamento_tokens_dia",
                    "rotina_minutos", "rotina", "modelo_base") if body.get(k) is not None}
                own = body.get("provedor") if isinstance(body.get("provedor"), dict) else None
                if own:
                    # provedor próprio: a chave vai para o arquivo protegido; a alma só guarda o nome da conexão
                    agent_id = souls.slugify(params.get("nome") or "")
                    if not agent_id:
                        raise ApiError(400, "dê um nome ao subagente")
                    if os.path.exists(souls.soul_path(agent_id)):
                        raise ApiError(400, "já existe um subagente com esse nome")
                    params["conexao"], params["modelo"] = cfg_call(conexoes.set_agent_provider, agent_id, own)
                r = paje_op("agents/create", 15, **params)
                if r.get("error") and own:
                    conexoes.remove_agent_provider(agent_id)
                return ok(r)
            r = ok(paje_op("agents/list", 10))
            r["modelos"] = paje_op("agents/templates", 5)
            return r
        if method == "POST" and act == "apagar":
            r = ok(paje_op("agents/delete", 10, id=arg))
            conexoes.remove_agent_provider(arg)          # a conexão só dele vai junto
            return r
        if method == "POST":
            upd = {k: v for k, v in body.items() if k != "provedor"}
            if "provedor" in body:
                own = body.get("provedor")
                if isinstance(own, dict):
                    upd["conexao"], upd["modelo"] = cfg_call(conexoes.set_agent_provider, arg, own)
                else:                                   # voltou a usar uma conexão compartilhada
                    conexoes.remove_agent_provider(arg)
                    upd.setdefault("conexao", "principal")
            return ok(paje_op("agents/update", 10, id=arg, **upd))
        r = ok(paje_op("agents/get", 10, id=arg))
        try:
            with open(r["agente"]["arquivo"], encoding="utf-8") as f:
                r["texto"] = f.read()
        except OSError:
            r["texto"] = ""
        r["provedor"] = (conexoes.agent_provider_public(arg)
                         if r["agente"].get("conexao") == conexoes.agent_conn_name(arg) else None)
        return r
    if sub == "trabalhos":
        if arg is None:
            if method == "POST":
                return ok(paje_op("tasks/create", 10, agente=body.get("agente"), titulo=body.get("titulo"),
                                  instrucoes=body.get("instrucoes"), origem="usuario"))
            return ok(paje_op("tasks/list", 10, limite=200))
        if method == "POST" and act == "cancelar":
            return ok(paje_op("tasks/cancel", 10, id=arg))
        return ok(paje_op("tasks/get", 10, id=arg))
    if sub == "harness":
        r = ok(paje_op("harness", 15))
        r["provedores"] = [{k: p[k] for k in ("id", "nome")} | {"url": bool(p.get("url")),
                                                                 "semchave": bool(p.get("semchave"))} for p in PRESETS]
        return r
    if sub == "alma" and method == "POST":
        return ok(paje_op("paje/soul", 10, texto=body.get("texto") or ""))
    if sub == "conexoes":
        if arg == "modelos" and method == "POST":
            provider, base, key = preset_params(body)
            return router_request("POST", "/tupi/models", {"provider": provider, "base_url": base, "api_key": key},
                                  timeout=40)
        if arg and act == "apagar" and method == "POST":
            if arg.startswith(conexoes.AGENT_PREFIX):
                raise ApiError(400, "esta é a conexão própria de um subagente: troque ou remova pelo formulário dele")
            edit_ai_config(lambda d: (d.get("conexoes") or {}).pop(arg, None))
            return {"ok": True}
        if arg and act == "modelos":
            return router_request("GET", "/v1/models?conexao=" + urllib.parse.quote(arg), timeout=40)
        if arg is None and method == "POST":
            nome = str(body.get("nome") or "").strip().lower()
            if not conexoes.NAME_RE.fullmatch(nome) or nome == "principal" or nome.startswith(conexoes.AGENT_PREFIX):
                raise ApiError(400, "nome da conexão: letras minúsculas, números, - ou _ (e não 'principal' nem "
                                    f"'{conexoes.AGENT_PREFIX}…', reservado aos provedores próprios dos subagentes)")
            provider, base, key = preset_params(body)
            entry = {"provider": provider, "base_url": base, "api_key": key, "model": str(body.get("model") or "")}
            edit_ai_config(lambda d: d.setdefault("conexoes", {}).__setitem__(nome, entry))
            return {"ok": True, "nome": nome}
        return {"conexoes": router_status().get("conexoes") or []}
    raise ApiError(404, "rota desconhecida")


def agent_api(app, method, parts, body):
    if not AGENT_IMPORT:
        return {"available": False, "error": "O agente (tupi-agent) não está instalado neste sistema."}
    head = parts[0]
    if head == "paje":
        return paje_api(method, parts, body)
    if head == "st":
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "promote" and method == "POST":
            if (ST_PANEL["ultima"] or {}).get("status") == "aguardando":
                return {"ok": False, "error": "Já há um pedido de promoção aguardando a sua aprovação."}
            threading.Thread(target=st_promote_from_panel, daemon=True).start()
            return {"ok": True}
        if sub == "discard" and method == "POST":
            return exec_op("st_discard", 300)
        s = exec_op("st_status", 60)
        s["ultima_promocao"] = ST_PANEL["ultima"]
        return s
    if head == "agent":
        sub = parts[1] if len(parts) > 1 else "state"
        if sub == "state":
            st = exec_op("status", 5)
            loop = load_json(LOOP_STATUS, {})
            sugs = [s for s in load_json(SUGGESTIONS, []) if s.get("status") == "nova"]
            return {"available": agent_available() and not st.get("error"), "error": st.get("error"),
                    "halted": st.get("halted", False), "pending": st.get("pending", []),
                    "mode": (st.get("policy") or {}).get("mode"), "livre": (st.get("policy") or {}).get("livre"),
                    "limits": st.get("limits"), "loop": loop, "suggestions_new": len(sugs),
                    "st": st.get("st"), "paje": paje_op("status", 3), "controle": st.get("controle"),
                    "jobs": [cid for cid, j in JOBS.items() if j.thread.is_alive()]}
        if sub == "decide":
            if body.get("decision") in ("once", "session"):
                item = next((p for p in exec_op("pending", 5).get("pending", []) if p.get("id") == body.get("id")), {})
                if "control_desktop" in (item.get("categories") or []):
                    # liberou o mouse/teclado: o Pajé sai da frente ANTES da primeira ação (senão ela cairia nele)
                    done = threading.Event()
                    GLib.idle_add(lambda: (app.step_aside(), done.set(), False)[2])
                    done.wait(3)
                    time.sleep(0.4)          # o gerenciador de janelas tira as janelas da tela
            return exec_op("decide", 10, id=body.get("id"), decision=body.get("decision"),
                           confirm_destructive=bool(body.get("confirm_destructive")))
        if sub == "devolver":           # "Devolver o controle": a IA para de usar o mouse/teclado
            return exec_op("revoke_control", 10)
        if sub == "stop":
            cancel_jobs()
            return exec_op("stop", 60)
        if sub == "resume":
            return exec_op("resume", 10)
        if sub == "undo":
            r = exec_op("undo", 900, action_id=body.get("action_id"))
            if r.get("ok") and body.get("conversation"):
                def mark(c):
                    for m in c["messages"]:
                        a = m.get("action") or {}
                        if a.get("action_id") == body.get("action_id"):
                            a.update(can_undo=False, undone=True)
                try:
                    update_conv(body["conversation"], mark)
                except ApiError:
                    pass
            return r
        if sub == "history":
            return exec_op("history", 10)
    if head == "policy":
        if len(parts) > 1 and parts[1] == "reset":
            return perm_op("policy_reset")
        if method == "POST":
            req = {k: body[k] for k in ("mode", "overrides", "livre_scope", "confirm") if k in body}
            return perm_op("policy_set", **req)
        return perm_op("policy_show")
    if head == "memory":
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "" and method == "GET":
            return {"facts": mcp("profile/list").get("items", []), "observations": mcp("observe/summary").get("items", [])}
        if sub == "set":
            return mcp("profile/set", key=body.get("key") or ("manual:" + uuid.uuid4().hex[:8]),
                       value=body.get("value", ""), source="você")
        if sub == "delete":
            return mcp("profile/delete", id=body.get("id"))
        if sub == "clear":
            return mcp("profile/clear")
        if sub == "forget":
            return mcp("observe/clear", **({"kind": str(body["kind"])} if body.get("kind") else {}))
    if head == "loop":
        cfg = json.loads(json.dumps(LOOP_DEFAULTS))
        cfg.update({k: v for k, v in load_json(LOOP_CFG, {}).items() if k in cfg})
        if method == "POST":
            for k in ("enabled", "silent"):
                if k in body:
                    cfg[k] = bool(body[k])
            for k, lo, hi in (("interval_min", 5, 240), ("daily_tokens", 0, 500000)):
                if k in body:
                    cfg[k] = max(lo, min(hi, int(body[k])))
            if isinstance(body.get("sources"), dict):
                cfg["sources"].update({k: bool(v) for k, v in body["sources"].items() if k in cfg["sources"]})
            save_json(LOOP_CFG, cfg)
        return {"settings": cfg, "status": load_json(LOOP_STATUS, {})}
    if head == "suggestions":
        sugs = load_json(SUGGESTIONS, [])
        if len(parts) == 1:
            for s in sugs:
                f = s.get("arquivo")
                if isinstance(f, dict) and f.get("path"):
                    p = os.path.expanduser(f["path"])
                    try:
                        with open(p, encoding="utf-8") as fh:
                            s["atual"] = fh.read()[:20000]
                    except OSError:
                        s["atual"] = None
            return {"suggestions": list(reversed(sugs))}
        sid, action = parts[1], body.get("action")
        s = next((x for x in sugs if x.get("id") == sid), None)
        if not s:
            raise ApiError(404, "sugestão não encontrada")
        if action == "dismiss":
            s["status"] = "dispensada"
            save_json(SUGGESTIONS, sugs)
            return {"ok": True}
        if action == "apply":
            s["status"] = "aplicando"
            save_json(SUGGESTIONS, sugs)
            apply_suggestion(app, s)
            return {"ok": True}
    raise ApiError(404, "rota desconhecida")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ------------------------------------------------------------- interface
def run_js(view, js):
    try:
        view.evaluate_javascript(js, -1, None, None, None, None, None)
    except (TypeError, AttributeError):
        view.run_javascript(js, None, None, None)


def make_webview(url):
    ctx = WebKit2.WebContext.get_default()
    ctx.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
    view = WebKit2.WebView.new_with_context(ctx)
    s = view.get_settings()
    s.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
    s.set_enable_developer_extras(bool(os.environ.get("TUPI_ASSISTANT_DEBUG")))
    s.set_javascript_can_access_clipboard(True)
    s.set_enable_write_console_messages_to_stdout(bool(os.environ.get("TUPI_ASSISTANT_DEBUG")))
    base = url.split("?")[0]

    def on_policy(v, decision, dtype):
        # Links externos abrem no navegador padrão, não dentro do app
        if dtype in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
            uri = decision.get_navigation_action().get_request().get_uri()
            if not uri.startswith(base):
                decision.ignore()
                try:
                    Gio.AppInfo.launch_default_for_uri(uri, None)
                except GLib.Error:
                    pass
                return True
        return False

    view.connect("decide-policy", on_policy)
    view.load_uri(url)
    return view


class Bubble(Gtk.Window):
    """Bolinha sempre visível, arrastável. Clique = mini chat; botão direito = menu."""
    SIZE = 62

    def __init__(self, app):
        super().__init__(title="Pajé (atalho)")
        self.app = app
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.stick()
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        self.composited = bool(visual and screen.is_composited())
        if self.composited:
            self.set_visual(visual)
        # A área de desenho define o tamanho real da janela (SIZE x SIZE). Sem um filho,
        # o GTK usa 200x200 e sobraria um quadrado invisível bloqueando cliques.
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.SIZE, self.SIZE)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK)
        self.area.connect("draw", self.on_draw)
        self.area.connect("button-press-event", self.on_press)
        self.area.connect("button-release-event", self.on_release)
        self.area.connect("motion-notify-event", self.on_motion)
        self.add(self.area)
        self.set_size_request(self.SIZE, self.SIZE)
        self.set_default_size(self.SIZE, self.SIZE)
        self.connect("configure-event", self.on_configure)
        self.connect("window-state-event", self.on_state)
        self.connect("realize", self.on_realize)
        self.press, self.dragging, self.save_id = None, False, 0
        pos = app.state.get("bubble")
        if pos:
            self.move(*pos)
        else:
            geo = self.monitor_geometry()
            self.move(geo.x + geo.width - self.SIZE - 24, geo.y + geo.height - self.SIZE - 70)

    def monitor_geometry(self):
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        return mon.get_workarea()

    def on_realize(self, *_):
        if not self.composited:
            # Sem compositor: recorta a janela em círculo (shape do X11)
            region = cairo.Region()
            r = self.SIZE / 2
            for y in range(self.SIZE):
                dy = y + 0.5 - r
                half = math.sqrt(max(0.0, r * r - dy * dy))
                x0 = int(round(r - half))
                region.union(cairo.RectangleInt(x0, y, max(1, int(round(2 * half))), 1))
            self.shape_combine_region(region)

    # cores por estado: (fundo topo, fundo base, anel/texto)
    THEMES = {
        "normal": ((0.10, 0.26, 0.16), (0.05, 0.14, 0.09), (0.32, 0.92, 0.14)),
        "livre": ((0.45, 0.06, 0.06), (0.25, 0.02, 0.02), (1.00, 0.33, 0.33)),
        "pending": ((0.42, 0.26, 0.02), (0.25, 0.14, 0.00), (1.00, 0.70, 0.15)),
        "halted": ((0.22, 0.22, 0.22), (0.12, 0.12, 0.12), (0.62, 0.62, 0.62)),
    }

    def set_state(self, state, thinking=False, working=False, livre=False):
        """livre: o modo livre fica visível (fundo vermelho + "LIVRE") mesmo com pedido pendente."""
        new = (state, thinking, working, livre)
        if new != (getattr(self, "state", None), getattr(self, "thinking", False), getattr(self, "working", False),
                   getattr(self, "livre", False)):
            self.state, self.thinking, self.working, self.livre = new
            self.area.queue_draw()
        tips = {"normal": "Pajé — clique para conversar; botão direito: menu",
                "livre": "ATENÇÃO: Pajé em MODO LIVRE — tudo liberado. Botão direito → PARAR IA.",
                "pending": "A IA está esperando sua aprovação — clique para ver",
                "halted": "Pajé PARADO — botão direito para retomar"}
        self.set_tooltip_text(tips.get(state, tips["normal"]) + ("\n(MODO LIVRE ATIVO)" if livre and state != "livre" else "")
                              + ("\n(modo proativo pensando…)" if thinking else ""))
        if state == "pending" and not getattr(self, "blink_id", 0):
            self.blink_id = GLib.timeout_add(500, self.blink)
        return False

    def blink(self):
        if getattr(self, "state", "") != "pending":
            self.blink_id, self.dim = 0, False
            self.area.queue_draw()
            return False
        self.dim = not getattr(self, "dim", False)
        self.area.queue_draw()
        return True

    def on_draw(self, _w, cr):
        s = self.SIZE
        top, bottom, ring = self.THEMES.get(getattr(self, "state", "normal"), self.THEMES["normal"])
        livre = getattr(self, "livre", False) and getattr(self, "state", "") != "halted"
        if livre:
            top, bottom = self.THEMES["livre"][:2]
        if getattr(self, "dim", False):
            ring = tuple(c * 0.55 for c in ring)
        # fundo transparente (com compositor) ou recortado em círculo (sem compositor)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        # sombra + círculo + anel
        cr.arc(s / 2, s / 2 + 1, s / 2 - 1, 0, 2 * math.pi)
        cr.set_source_rgba(0, 0, 0, 0.35)
        cr.fill()
        cr.arc(s / 2, s / 2, s / 2 - 3, 0, 2 * math.pi)
        grad = cairo.LinearGradient(0, 0, 0, s)
        grad.add_color_stop_rgb(0, *top)
        grad.add_color_stop_rgb(1, *bottom)
        cr.set_source(grad)
        cr.fill_preserve()
        cr.set_source_rgb(*ring)
        cr.set_line_width(3)
        cr.stroke()
        label = "!" if getattr(self, "state", "") == "pending" else "■" if getattr(self, "state", "") == "halted" else "Pajé"
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(14 if label == "Pajé" else 26)
        ext = cr.text_extents(label)
        cr.move_to(s / 2 - ext.width / 2 - ext.x_bearing, s / 2 - ext.height / 2 - ext.y_bearing)
        cr.set_source_rgb(*ring)
        cr.show_text(label)
        # modo proativo pensando / agente trabalhando: pontinho azul no canto
        if getattr(self, "thinking", False) or getattr(self, "working", False):
            cr.arc(s - 12, 12, 6, 0, 2 * math.pi)
            cr.set_source_rgb(0.25, 0.62, 1.0) if self.thinking else cr.set_source_rgb(0.32, 0.92, 0.14)
            cr.fill_preserve()
            cr.set_source_rgb(0.05, 0.05, 0.05)
            cr.set_line_width(1.5)
            cr.stroke()
        if livre:
            cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(8)
            ext = cr.text_extents("LIVRE")
            cr.move_to(s / 2 - ext.width / 2 - ext.x_bearing, s - 9)
            cr.set_source_rgb(1, 0.85, 0.85)
            cr.show_text("LIVRE")
        return True

    def on_press(self, _w, ev):
        if ev.button == 1:
            self.press = (ev.x_root, ev.y_root, ev.time)
            self.dragging = False
        elif ev.button == 3:
            self.app.popup_menu(ev)
        return True

    def on_motion(self, _w, ev):
        if self.press and not self.dragging:
            dx, dy = ev.x_root - self.press[0], ev.y_root - self.press[1]
            if dx * dx + dy * dy > 36:
                self.dragging = True
                self.begin_move_drag(1, int(self.press[0]), int(self.press[1]), ev.time)
        return True

    def on_release(self, _w, ev):
        if ev.button == 1 and self.press and not self.dragging:
            self.app.toggle_mini()
        self.press = None
        return True

    def on_configure(self, *_):
        if self.save_id:
            GLib.source_remove(self.save_id)
        self.save_id = GLib.timeout_add(600, self.save_position)
        return False

    def save_position(self):
        self.save_id = 0
        self.app.state["bubble"] = list(self.get_position())
        self.app.save_state()
        return False

    def on_state(self, _w, ev):
        # "Mostrar área de trabalho" / minimizar não pode sumir com a bolinha
        if ev.new_window_state & Gdk.WindowState.ICONIFIED:
            GLib.timeout_add(300, lambda: (self.deiconify(), self.present(), False)[-1])
        return False


CONTROL_CSS = b"""
.tupi-control { background: #7a2e00; border-radius: 0 0 12px 12px; padding: 4px 10px; }
.tupi-control label.msg { color: #fff; font-weight: bold; }
.tupi-control button label { color: #222; }
.tupi-control button.stop label { color: #fff; }
.tupi-control button { padding: 2px 10px; border-radius: 8px; }
.tupi-control button.stop { background: #b3261e; color: #fff; }
"""


class ControlBar(Gtk.Window):
    """Faixa no topo da tela enquanto a IA usa o mouse e o teclado (assistido): quem está no controle e como
    retomar. É uma janela do Pajé: a IA não consegue clicar nela."""

    def __init__(self, app):
        super().__init__(title="Pajé — controle da tela")
        self.app = app
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)      # não rouba o foco da janela onde a IA está digitando
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.stick()
        prov = Gtk.CssProvider()
        prov.load_from_data(CONTROL_CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.get_style_context().add_class("tupi-control")
        self.label = Gtk.Label(label="")
        self.label.get_style_context().add_class("msg")
        give = Gtk.Button(label="Devolver o controle")
        give.connect("clicked", lambda *_: threading.Thread(target=exec_op, args=("revoke_control", 10),
                                                            daemon=True).start())
        stop = Gtk.Button(label="■ PARAR IA")
        stop.get_style_context().add_class("stop")
        stop.connect("clicked", lambda *_: threading.Thread(
            target=lambda: (cancel_jobs(), exec_op("stop", 60)), daemon=True).start())
        for w in (self.label, give, stop):
            box.pack_start(w, False, False, 0)
        self.add(box)
        self.text = None

    def update(self, ctl):
        text = (f"🖱  {ctl.get('agente') or 'Pajé'} está usando o mouse e o teclado — mexa o mouse para retomar"
                if ctl.get("ativo") else
                f"🖱  {ctl.get('agente') or 'Pajé'} pode usar o mouse e o teclado nesta conversa")
        if text != self.text:
            self.text = text
            self.label.set_text(text)
        self.show_all()
        w, _ = self.get_size()
        geo = Gdk.Display.get_default().get_monitor(0).get_geometry()
        self.move(geo.x + max(0, (geo.width - w) // 2), geo.y)


class Assistant(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.server = self.port = None
        self.bubble = self.mini = self.full = None
        self.control_bar = None
        self.stepped_aside = False      # janelas minimizadas enquanto a IA usa o mouse/teclado
        self.stepped_at = 0.0
        self.restore = []
        self.state = load_json(STATE_FILE, {})
        self.recorder = Recorder()

    # ---------------------------------------------------------- ciclo
    def do_startup(self):
        Gtk.Application.do_startup(self)
        os.makedirs(CONV_DIR, exist_ok=True)
        os.makedirs(ATT_DIR, exist_ok=True)
        self.start_server()
        self.hold()   # continua rodando com as janelas escondidas (a bolinha é a "casa")
        self.tray = None
        self.seen_pending = set()
        self.agent_state = {}
        threading.Thread(target=self.poll_agent, daemon=True).start()

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        if "--quit" in args:
            self.quit()
            return 0
        if "--send" in args:
            # tupi-assistant --send "pergunta": roda o agente e imprime a resposta neste terminal
            i = args.index("--send")
            text = " ".join(args[i + 1:]).strip()
            if not text:
                command_line.print_literal("uso: tupi-assistant --send \"sua pergunta\"\n")
                return 2
            conv = new_conv()
            try:
                send_message(conv["id"], text, [], None, cmdline=command_line)
            except ApiError as e:
                command_line.print_literal(e.message + "\n")
                return 1
            return 0
        if not self.bubble and "--no-bubble" not in args:
            self.bubble = Bubble(self)
            self.bubble.show_all()
        tab = next((a.split("=", 1)[1] for a in args if a.startswith("--settings=")), None)
        if "--settings" in args or tab:   # tupi-assistant --settings[=agentes|trabalhos|harness|st|...]
            self.open_settings(tab)
        elif "--mini" in args:
            self.show_mini()
        elif "--bubble" not in args:
            self.show_full()
        return 0

    # ------------------------------------------- estado do agente (cores)
    def poll_agent(self):
        while True:
            try:
                st = agent_api(self, "GET", ["agent", "state"], {}) if AGENT_IMPORT else {}
            except Exception:
                st = {}
            GLib.idle_add(self.apply_agent_state, st)
            time.sleep(1.5)

    def apply_agent_state(self, st):
        self.agent_state = st
        if st.get("halted"):
            cancel_jobs()
        pending = st.get("pending") or []
        state = ("halted" if st.get("halted") else "pending" if pending
                 else "livre" if st.get("mode") == "livre" else "normal")
        thinking = (st.get("loop") or {}).get("state") in ("pensando", "observando")
        working = bool(st.get("jobs")) or bool((st.get("paje") or {}).get("rodando"))   # subagentes trabalhando
        if self.bubble:
            self.bubble.set_state(state, thinking, working, st.get("mode") == "livre")
        self.update_tray(state, st)
        ctl = st.get("controle")
        on = bool(ctl and (ctl.get("ativo") or ctl.get("liberado")) and not st.get("halted"))
        if on:
            if not self.control_bar:
                self.control_bar = ControlBar(self)
            self.control_bar.update(ctl)
        elif self.control_bar and self.control_bar.get_visible():
            self.control_bar.hide()
        # a IA está usando a tela: as janelas do Pajé saem da frente; quando ela termina, voltam
        last = (ctl or {}).get("ultima") or 0
        idle = time.time() - max(self.stepped_at, last)
        if on and time.time() - last < 3 and not self.stepped_aside:
            self.step_aside()
        elif self.stepped_aside and ((not on and time.time() - self.stepped_at > 4) or
                                     (not self.control_job_running(ctl) and idle > 3) or idle > 120):
            # volta quando o trabalho que usa a tela TERMINOU (a conversa ou o trabalho do subagente), não no
            # meio dele: um modelo que pensa devagar não pode ter o Pajé pulando na frente do que está fazendo.
            # A margem de 4 s: logo depois de você liberar, o executor ainda está registrando a liberação.
            if os.environ.get("TUPI_ASSISTANT_DEBUG"):
                print(f"[controle] volta: on={on} idle={idle:.1f} working={working}", flush=True)
            self.step_back()
        # Pedido novo de aprovação e nenhuma janela aberta: abre o chat rápido para mostrar
        new = {p["id"] for p in pending} - self.seen_pending
        self.seen_pending = {p["id"] for p in pending}
        visible = (self.full and self.full.get_visible()) or (self.mini and self.mini.get_visible())
        if new and not visible and self.bubble and self.bubble.get_visible():
            self.show_mini()
        return False

    def control_job_running(self, ctl):
        """O trabalho que está usando a tela ainda está rodando? (conversa do Pajé ou trabalho de subagente)"""
        sess = (ctl or {}).get("sessao") or ""
        if not sess:                       # liberado, mas a primeira ação ainda não veio
            return time.time() - self.stepped_at < 60
        if sess.startswith("tarefa-"):
            t = (paje_op("tasks/get", 3, id=sess[len("tarefa-"):]).get("trabalho") or {})
            return t.get("status") in ("na_fila", "rodando")
        job = JOBS.get(sess)
        return bool(job and job.thread.is_alive())

    def step_aside(self):
        """O Pajé sai da frente enquanto a IA usa a tela (fica a bolinha e a faixa de controle)."""
        self.stepped_at = time.time()
        if self.stepped_aside:
            return
        self.stepped_aside, self.restore = True, []
        if self.full and self.full.get_visible():
            self.full.iconify()
            self.restore.append("full")
        if self.mini and self.mini.get_visible():
            self.mini.hide()
            self.restore.append("mini")

    def step_back(self):
        """O controle terminou: as janelas do Pajé voltam (com a resposta da IA)."""
        self.stepped_aside = False
        if "full" in self.restore and self.full:
            self.full.deiconify()
            self.full.present()
        if "mini" in self.restore:
            self.show_mini()
        self.restore = []

    def update_tray(self, state, st):
        if not AGENT_IMPORT:
            return
        # Gtk.StatusIcon é "deprecated" no GTK3, mas é o que a área de notificação do XFCE mostra
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self._update_tray(state, st)

    def _update_tray(self, state, st):
        if self.tray is None:
            self.tray = Gtk.StatusIcon()
            self.tray.connect("activate", lambda *_: self.show_full())
            self.tray.connect("popup-menu", lambda icon, button, t: self.popup_menu(None, button, t))
        colors = {"normal": (0.32, 0.92, 0.14), "livre": (1.0, 0.25, 0.25), "pending": (1.0, 0.7, 0.15),
                  "halted": (0.6, 0.6, 0.6)}
        if st.get("mode") == "livre" and state == "pending":
            state = "livre"   # no ícone da bandeja o vermelho do modo livre tem prioridade
        key = (state, st.get("mode"))
        if getattr(self, "tray_key", None) != key:
            self.tray_key = key
            size = 24
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
            cr = cairo.Context(surf)
            cr.arc(12, 12, 10, 0, 2 * math.pi)
            cr.set_source_rgb(0.08, 0.08, 0.08)
            cr.fill_preserve()
            cr.set_source_rgb(*colors[state])
            cr.set_line_width(2.5)
            cr.stroke()
            cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(9)
            ext = cr.text_extents("PJ")
            cr.move_to(12 - ext.width / 2 - ext.x_bearing, 12 - ext.height / 2 - ext.y_bearing)
            cr.show_text("PJ")
            self.tray.set_from_pixbuf(Gdk.pixbuf_get_from_surface(surf, 0, 0, size, size))
        mode = (st.get("mode") or "?").upper()
        tip = {"livre": f"ATENÇÃO — Pajé: MODO LIVRE ATIVO ({(st.get('livre') or {}).get('scope', '')})",
               "pending": "Pajé: aguardando sua aprovação", "halted": "Pajé: PARADO"}.get(
            state, f"Pajé: modo {mode}")
        self.tray.set_tooltip_text(tip)
        self.tray.set_visible(True)
        if self.full:
            self.full.set_title("Pajé — MODO LIVRE ATIVO" if state == "livre" else "Pajé")

    def stop_ai(self):
        cancel_jobs()
        threading.Thread(target=lambda: exec_op("stop", 60), daemon=True).start()

    def resume_ai(self):
        threading.Thread(target=lambda: exec_op("resume", 10), daemon=True).start()

    def start_server(self):
        Handler.app = self
        self.server = Server(("127.0.0.1", FIXED_PORT), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"[Pajé] interface em http://127.0.0.1:{self.port}/", flush=True)

    def url(self, mode):
        return f"http://127.0.0.1:{self.port}/?mode={mode}&token={TOKEN}"

    def save_state(self):
        save_json(STATE_FILE, self.state)

    # -------------------------------------------------------- janelas
    def show_full(self, extra=""):
        if not self.full:
            self.full = Gtk.ApplicationWindow(application=self, title="Pajé")
            self.full.set_icon_name("tupios-ia")
            self.full.set_default_size(1100, 720)
            self.full.add(make_webview(self.url("full") + extra))
            self.full.connect("delete-event", lambda w, e: (w.hide(), True)[1])
            self.full.show_all()
        self.full.present()

    def show_mini(self):
        if not self.mini:
            self.mini = Gtk.Window(title="Pajé — rápido")
            self.mini.set_icon_name("tupios-ia")
            self.mini.set_decorated(False)
            self.mini.set_keep_above(True)
            self.mini.set_skip_taskbar_hint(True)
            self.mini.set_skip_pager_hint(True)
            self.mini.set_default_size(400, 560)
            self.mini.set_type_hint(Gdk.WindowTypeHint.UTILITY)
            self.mini_view = make_webview(self.url("mini"))
            self.mini.add(self.mini_view)
            self.mini.connect("delete-event", lambda w, e: (w.hide(), True)[1])
            self.mini.connect("key-press-event", self.on_mini_key)
        self.place_mini()
        self.mini.show_all()
        self.mini.present()
        self.mini_view.grab_focus()
        run_js(self.mini_view, "window.tupiFocus && window.tupiFocus()")

    def on_mini_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.mini.hide()
            return True
        return False

    def place_mini(self):
        """Abre a mini janela ao lado da bolinha, sem sair da tela."""
        w, h = 400, 560
        if not self.bubble:
            return
        bx, by = self.bubble.get_position()
        geo = self.bubble.monitor_geometry()
        x = bx - w - 12 if bx - w - 12 >= geo.x else bx + Bubble.SIZE + 12
        y = by + Bubble.SIZE - h
        x = max(geo.x + 8, min(x, geo.x + geo.width - w - 8))
        y = max(geo.y + 8, min(y, geo.y + geo.height - h - 8))
        self.mini.move(x, y)

    def toggle_mini(self):
        if self.mini and self.mini.get_visible():
            self.mini.hide()
        else:
            self.show_mini()

    def popup_menu(self, ev, button=None, activate_time=None):
        menu = Gtk.Menu()
        halted = self.agent_state.get("halted")
        items = [
            ("Abrir o Pajé (completo)", self.show_full),
            ("Chat rápido", self.show_mini),
            (None, None),
            ("Tirar print e perguntar à IA", lambda: self.quick_shot("full")),
            ("Recortar parte da tela e perguntar", lambda: self.quick_shot("region")),
            (None, None),
        ]
        if AGENT_IMPORT:
            items.append(("▶  Retomar o Pajé" if halted else "■  PARAR IA (cancela tudo agora)",
                          self.resume_ai if halted else self.stop_ai))
            items.append(("Permissões, agentes e harness do Pajé…", lambda: self.open_settings()))
        items += [
            ("Configurar IA (provedor, chave, modelo)", lambda: self.window_action("open_config")),
            ("Esconder a bolinha (volta no próximo login)", lambda: self.bubble and self.bubble.hide()),
        ]
        for label, fn in items:
            if label is None:
                menu.append(Gtk.SeparatorMenuItem())
                continue
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _i, f=fn: f())
            menu.append(item)
        menu.show_all()
        if ev is not None:
            menu.attach_to_widget(self.bubble, None)
            menu.popup_at_pointer(ev)
        else:
            menu.popup(None, None, None, None, button or 0, activate_time or Gtk.get_current_event_time())
        self._menu = menu   # mantém referência enquanto aberto

    SETTINGS_TABS = ("perms", "agentes", "trabalhos", "harness", "st", "memory", "loop", "sugs", "history")

    def open_settings(self, tab=None):
        tab = tab if tab in self.SETTINGS_TABS else "perms"
        if not self.full:
            self.show_full(extra="&open=" + tab)   # a página abre direto na aba pedida
        else:
            self.show_full()
            run_js(self.full.get_child(), f"window.tupiSettings && window.tupiSettings({json.dumps(tab)})")

    def quick_shot(self, mode):
        """Pelo menu da bolinha: abre o chat rápido e anexa o print."""
        self.show_mini()
        js = f"window.tupiScreenshot && window.tupiScreenshot('{mode}')"
        GLib.timeout_add(300, lambda: (run_js(self.mini_view, js), False)[1])

    def window_action(self, action):
        def do():
            if action == "open_full":
                if self.mini:
                    self.mini.hide()
                self.show_full()
            elif action == "close_mini" and self.mini:
                self.mini.hide()
            elif action == "open_settings":
                if self.mini:
                    self.mini.hide()
                self.open_settings()
            elif action == "open_config":
                try:
                    subprocess.Popen([CONFIG_GUI], start_new_session=True)
                except OSError:
                    pass
            return False
        GLib.idle_add(do)

    # --------------------------------------------------------- prints
    def on_main(self, fn):
        """Executa fn na thread da interface e espera o resultado."""
        done, box = threading.Event(), {}

        def wrapper():
            try:
                box["v"] = fn()
            except Exception as e:  # repassa o erro para a thread do servidor
                box["e"] = e
            done.set()
            return False
        GLib.idle_add(wrapper)
        done.wait(30)
        if "e" in box:
            raise box["e"]
        return box.get("v")

    def take_screenshot(self, mode):
        os.makedirs(pictures_dir(), exist_ok=True)
        path = os.path.join(pictures_dir(), datetime.now().strftime("print-%Y-%m-%d_%H-%M-%S.png"))
        visible = self.on_main(lambda: [w for w in (self.mini, self.full, self.bubble) if w and w.get_visible()])
        self.on_main(lambda: [w.hide() for w in visible])
        try:
            time.sleep(0.6)   # tempo para as janelas sumirem da tela
            if mode == "region" and shutil.which("xfce4-screenshooter"):
                r = run(["xfce4-screenshooter", "-r", "-s", path], timeout=180)
                if r.returncode != 0 or not os.path.exists(path):
                    raise ApiError(400, "Recorte cancelado.")
            else:
                def grab():
                    root = Gdk.get_default_root_window()
                    pb = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
                    if not pb:
                        raise ApiError(500, "Não foi possível capturar a tela.")
                    pb.savev(path, "png", [], [])
                self.on_main(grab)
        finally:
            self.on_main(lambda: [w.show() for w in visible])
            if self.mini in visible:
                self.on_main(lambda: self.mini.present())
        return path


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = Assistant()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
