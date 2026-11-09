"""Servidores falsos de LLM para testes (Anthropic Messages e OpenAI-compatível).

Anthropic (porta A): responde com thinking assinado + tool_use quando há ferramentas e a
última mensagem do usuário não é tool_result; depois de tool_result responde com texto.
OpenAI (porta O): "agente roteirizado" — escolhe a ferramenta por palavras-chave da
última mensagem do usuário (usado no teste de ponta a ponta na VM).
Todas as requisições recebidas são gravadas (JSON por linha) no arquivo de log.
"""
import json
import re
import sys
import threading
import http.server

LOG = sys.argv[1]
PORT_A = int(sys.argv[2]) if len(sys.argv) > 2 else 19100
PORT_O = int(sys.argv[3]) if len(sys.argv) > 3 else 19101


def record(api, handler, body):
    with open(LOG, "a") as f:
        f.write(json.dumps({"api": api, "path": handler.path,
                            "headers": {k.lower(): v for k, v in handler.headers.items()},
                            "body": body}) + "\n")


class Base(http.server.BaseHTTPRequestHandler):
    def _j(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")

    def log_message(self, *a):
        pass


class Anthropic(Base):
    def do_GET(self):
        record("anthropic", self, None)
        self._j(200, {"data": [{"id": "claude-opus-5-5", "type": "model"}], "has_more": False})

    def do_POST(self):
        body = self._body()
        record("anthropic", self, body)
        last = body["messages"][-1]
        content = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
        results = [b for b in content if b.get("type") == "tool_result"]
        text = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
        base = {"id": "msg_x", "type": "message", "role": "assistant", "model": body["model"],
                "usage": {"input_tokens": 10, "output_tokens": 5}}
        if results:
            joined = " | ".join(str(r.get("content"))[:80] for r in results)
            return self._j(200, dict(base, stop_reason="end_turn", content=[
                {"type": "thinking", "thinking": "", "signature": "sig-final"},
                {"type": "text", "text": f"Resultado recebido ({len(results)}): {joined}"}]))
        if "RECUSE" in text:
            return self._j(200, dict(base, stop_reason="refusal", stop_details={"category": "cyber"}, content=[
                {"type": "tool_use", "id": "toolu_cortado", "name": "run_command", "input": {"command": "rm"}}]))
        if body.get("tools"):
            calls = [{"type": "tool_use", "id": "toolu_A1", "name": "list_dir", "input": {"path": "~"}}]
            if "DUAS" in text:
                calls.append({"type": "tool_use", "id": "toolu_A2", "name": "system_info", "input": {}})
            return self._j(200, dict(base, stop_reason="tool_use", content=[
                {"type": "thinking", "thinking": "", "signature": "sig-123"},
                {"type": "text", "text": "Vou olhar."}] + calls))
        self._j(200, dict(base, stop_reason="end_turn", content=[{"type": "text", "text": "Sem ferramentas."}]))


SCRIPT = [
    ("liste os arquivos", "list_dir", {"path": "~"}),
    ("instale o pacote", "install_package", {"name": "hello"}),
    ("apague /etc/fstab", "run_command", {"command": "rm /etc/fstab"}),
    ("info do sistema", "system_info", {}),
    ("crie o arquivo", "write_file", {"path": "~/teste-tupi.txt", "content": "criado pela IA\n"}),
    ("descarte o ensaio", "st_discard", {}),
    ("rode", None, None),   # "rode <comando>" -> run_command
]
MUDE = re.compile(r"mude\s+([a-z0-9_.\-]+)\s+para\s+(\S+)", re.I)


def plan(user, tools=()):
    """Pedido do usuário -> lista de chamadas de ferramenta (uma por passo) ou None."""
    low = (user or "").lower()
    # usar o computador (mouse/teclado): coordenadas na imagem do print, como um modelo de verdade faria
    m = re.search(r"clique em (\d+),(\d+), demore e escreva (.+)", user or "")
    if m and "use_computer" in tools:      # um modelo lento: 20 s entre o clique e o texto
        return [("use_computer", {"action": "screenshot"}),
                ("use_computer", {"action": "click", "x": int(m.group(1)), "y": int(m.group(2))}),
                ("use_computer", {"action": "wait", "seconds": 10}), ("use_computer", {"action": "wait", "seconds": 10}),
                ("use_computer", {"action": "type", "text": m.group(3).strip()}),
                ("use_computer", {"action": "key", "keys": "ctrl+s"})]
    m = re.search(r"clique em (\d+),(\d+) e escreva (.+)", user or "")
    if m and "use_computer" in tools:
        return [("use_computer", {"action": "screenshot"}),
                ("use_computer", {"action": "click", "x": int(m.group(1)), "y": int(m.group(2))}),
                ("use_computer", {"action": "type", "text": m.group(3).strip()}),
                ("use_computer", {"action": "key", "keys": "ctrl+s"}),
                ("use_computer", {"action": "screenshot"})]
    m = re.search(r"s[óo] clique em (\d+),(\d+)", low)
    if m and "use_computer" in tools:
        return [("use_computer", {"action": "click", "x": int(m.group(1)), "y": int(m.group(2))})]
    m = re.search(r"abra o aplicativo (\S+)", low)
    if m and "open_app" in tools:
        return [("open_app", {"app": m.group(1).strip(".,!")})]
    m = re.search(r"s[óo] digite (.+)", user or "")
    if m and "use_computer" in tools:
        return [("use_computer", {"action": "type", "text": m.group(1).strip()})]
    m = re.search(r"abra o arquivo (\S+)", user or "")
    if m and "open_file" in tools:
        return [("open_file", {"path": m.group(1)}), ("use_computer", {"action": "wait", "seconds": 3}),
                ("use_computer", {"action": "screenshot"})]
    if "três prints" in low and "use_computer" in tools:
        return [("use_computer", {"action": "screenshot"})] * 3
    if "delegar_tarefas" in tools:          # o Pajé (tem as ferramentas de orquestração)
        if "monte uma equipe" in low:
            return [("criar_subagente", {"nome": "Pesquisador", "papel": "Procura e lista arquivos",
                                         "alma": "# Quem eu sou\nSou o Pesquisador.\n# Como trabalho\n- Listo e resumo.",
                                         "ferramentas": ["list_dir", "run_command"]}),
                    ("criar_subagente", {"nome": "Analista", "papel": "Olha o estado do sistema",
                                         "alma": "# Quem eu sou\nSou o Analista.", "ferramentas": ["system_info",
                                                                                               "run_command"]}),
                    ("delegar_tarefas", {"tarefas": [
                        {"agente": "Pesquisador", "titulo": "Listar a pasta", "instrucoes": "rode sleep 4 && touch ~/pesquisa.txt"},
                        {"agente": "Analista", "titulo": "Ver o sistema", "instrucoes": "rode sleep 4 && touch ~/analise.txt"}]}),
                    ("aguardar_tarefas", {"segundos": 120})]
        m = re.search(r"delegue para o (\w+)(?::\s*(.*))?", low)
        if m:
            instr = (m.group(2) or "liste os arquivos da pasta").strip()
            return [("delegar_tarefas", {"tarefas": [{"agente": m.group(1), "titulo": instr[:40], "instrucoes": instr}]}),
                    ("aguardar_tarefas", {"segundos": 120})]
        if "sua equipe" in low:
            return [("listar_agentes", {})]
    if "o que é o st" in low or "o que e o st" in low:
        return [("system_info", {})]
    m = re.search(r"teste no st e depois aplique: instale o (\S+)", low)
    if m:
        pkg = m.group(1).strip(".,!")
        return [("st_stage_change", {"descricao": f"instalar o {pkg}", "pacotes": [pkg]}), ("st_diff", {}),
                ("st_promote", {})]
    if low.startswith("rode no st:"):
        rest = user.split(":", 1)[1].strip()
        mm = MUDE.search(rest)
        return [("st_run", {"command": f"sysctl -w {mm.group(1)}={mm.group(2)}" if mm else rest})]
    mm = MUDE.search(user or "")
    if mm:
        return [("set_kernel_param", {"name": mm.group(1), "value": mm.group(2).strip(".,!")})]
    for key, tool, args in SCRIPT:
        if key in low:
            if tool is None:
                tool, args = "run_command", {"command": user[low.index("rode") + 5:].strip()}
            return [(tool, args)]
    return None


class OpenAI(Base):
    def do_GET(self):
        record("openai", self, None)
        self._j(200, {"object": "list", "data": [{"id": "agente-roteirizado"}]})

    def do_POST(self):
        body = self._body()
        record("openai", self, body)
        msgs = body.get("messages", [])
        last = msgs[-1] if msgs else {}

        def answer(text):
            return self._j(200, {"id": "c1", "object": "chat.completion", "model": body.get("model"),
                                 "choices": [{"index": 0, "finish_reason": "stop",
                                              "message": {"role": "assistant", "content": text}}]})

        def text_of(m):
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
        # pedido = última mensagem do usuário (sem contar os prints automáticos da tela); passo = quantas
        # ferramentas já rodaram depois dela
        idx = max((i for i, m in enumerate(msgs) if m.get("role") == "user"
                   and not text_of(m).lstrip().startswith("(print")), default=-1)
        user = text_of(msgs[idx]) if idx >= 0 else ""
        done = [m for m in msgs[idx + 1:] if m.get("role") == "tool"]
        names = [t.get("function", {}).get("name") for t in body.get("tools") or []]
        steps = plan(user, names) if body.get("tools") else None
        if steps and len(done) < len(steps) and not (done and done[-1].get("content", "").startswith("AÇÃO NÃO")):
            tool, args = steps[len(done)]
            return self._j(200, {"id": "c2", "object": "chat.completion", "model": body.get("model"),
                                 "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                                     "role": "assistant", "content": None, "tool_calls": [{
                                         "id": f"call_{len(done) + 1}", "type": "function",
                                         "function": {"name": tool, "arguments": json.dumps(args)}}]}}]})
        if last.get("role") == "tool":
            low = user.lower()
            if "o que é o st" in low or "o que e o st" in low:
                system = text_of(msgs[0]) if msgs and msgs[0].get("role") == "system" else ""
                arch = system[system.find("Arquitetura do TUPIOS"):system.find("Regras para mudanças")].strip()
                return answer("[resposta roteirizada a partir do prompt do sistema] " + arch +
                              "\nEstado do ST agora (system_info): " + str(last.get("content"))[-1800:])
            return answer("Feito. Resultado da ferramenta: " + str(last.get("content"))[:1500])
        return answer("Olá! (agente roteirizado)")


for port, h in ((PORT_A, Anthropic), (PORT_O, OpenAI)):
    threading.Thread(target=http.server.HTTPServer(("127.0.0.1", port), h).serve_forever, daemon=True).start()
threading.Event().wait()
