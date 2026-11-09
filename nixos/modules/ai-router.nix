# TUPIOS — AI Router Module
# Liga o sistema a um modelo de IA, com vários provedores:
#   - "openai":    qualquer API compatível com OpenAI (OpenAI, Groq, OpenRouter, DeepSeek, Gemini...)
#   - "anthropic": Claude, pelo SDK oficial da Anthropic (Messages API)
#   - "ollama":    IA local (opcional, precisa de services.ollama)
# Inclui:
#   - tupi-ai-router.service  (sobe no boot, escuta em 127.0.0.1:8080, formato OpenAI)
#   - tupi-ai-config          (lista modelos do provedor, salva a configuração; `setup` = assistente no terminal)
#   - tupi-ai-setup           (atalho para `tupi-ai-config setup`)
#   - tupi-ask                (pergunta à IA pelo terminal; sem argumentos abre um chat)
#   - tupi-ai-config-gui / tupi-ai-chat + atalhos no menu (quando há interface gráfica)
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.aiRouter;

  routerUrl = "http://127.0.0.1:${toString cfg.port}";

  # Python do router com o SDK oficial da Anthropic (usado só no provedor "anthropic")
  routerPython = pkgs.python3.withPackages (ps: [ ps.anthropic ]);

  routerScript = pkgs.writeText "tupi-ai-router.py" ''
    #!/usr/bin/env python3
    """TUPIOS AI Router — proxy no formato OpenAI para vários provedores de IA."""
    import json, os, sys, time, uuid, socketserver, http.server, urllib.request, urllib.error

    import anthropic

    CONFIG_PATH = os.environ.get("TUPI_AI_CONFIG", "/etc/tupi/ai-config.json")
    HOST = os.environ.get("ROUTER_HOST", "127.0.0.1")
    PORT = int(os.environ.get("ROUTER_PORT", "8080"))
    OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    DEFAULT_BACKEND = os.environ.get("TUPI_DEFAULT_BACKEND", "remote")
    DEFAULT_MODEL = os.environ.get("TUPI_DEFAULT_MODEL", "gpt-4o-mini")
    DEFAULT_ANTHROPIC_MODEL = os.environ.get("TUPI_DEFAULT_ANTHROPIC_MODEL", "claude-opus-5-5")
    DEFAULT_LOCAL_MODEL = os.environ.get("TUPI_DEFAULT_LOCAL_MODEL", "llama3.2")
    DEFAULT_BASE = os.environ.get("TUPI_DEFAULT_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    TIMEOUT = int(os.environ.get("TUPI_AI_TIMEOUT", "300"))
    UA = "TUPIOS-AI-Router/0.2"

    # Anthropic: max_tokens é obrigatório na Messages API
    ANTHROPIC_MAX_TOKENS = 16000
    # Modelos que aceitam o fallback automático em caso de recusa de segurança
    # (beta server-side-fallback-2026-07-01 + "fallbacks": "default")
    ANTHROPIC_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

    NOT_CONFIGURED = ("IA não configurada: abra 'Configurar IA' no menu Iniciar "
                      "(ou rode tupi-ai-setup no terminal).")

    # Ids que não são modelos de chat (embeddings, áudio, imagem...) ficam fora da lista
    NON_CHAT = ("embed", "tts", "whisper", "dall-e", "moderation", "realtime", "audio",
                "transcribe", "image", "davinci", "babbage", "sora", "search", "guard")

    PREFERRED = {
        "anthropic": ["claude-opus-5-5"],
        "openai": ["gpt-4o-mini"],
    }

    class UpstreamError(Exception):
        def __init__(self, status, message, kind="upstream_error"):
            super().__init__(message)
            self.status, self.message, self.kind = status, message, kind

    # ---------------------------------------------------------------- config
    def load_config():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            return {}
        except OSError as e:
            return {"_error": f"não foi possível ler {CONFIG_PATH}: {e}"}
        if not text.strip():
            return {}
        try:
            data = json.loads(text)
        except ValueError as e:
            return {"_error": f"{CONFIG_PATH} inválido: {e}"}
        return normalize(data) if isinstance(data, dict) else {}

    def normalize(cfg):
        """Formato novo (provider/api_key/base_url/model) ou antigo (default_backend/remote_*)."""
        if "provider" in cfg:
            return cfg
        if cfg.get("default_backend") == "local":
            return {"provider": "ollama", "model": cfg.get("model")}
        return {"provider": "openai", "api_key": cfg.get("remote_api_key", ""),
                "base_url": cfg.get("remote_base_url"), "model": cfg.get("model")}

    def resolve(cfg):
        """(provider, base_url, api_key, model) — ou None se ainda não configurado."""
        provider = cfg.get("provider") or ("ollama" if DEFAULT_BACKEND == "local" else "openai")
        key = (cfg.get("api_key") or "").strip()
        base = (cfg.get("base_url") or "").rstrip("/")
        model = cfg.get("model") or ""
        if provider == "ollama":
            return provider, base or OLLAMA + "/v1", "", model or DEFAULT_LOCAL_MODEL
        if not key:
            return None
        if provider == "anthropic":
            return provider, base, key, model or DEFAULT_ANTHROPIC_MODEL
        return "openai", base or DEFAULT_BASE, key, model or DEFAULT_MODEL

    # -------------------------------------------- OpenAI-compatible (HTTP)
    def http_call(method, url, headers=None, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", UA)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, r.read(), r.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            return e.code, e.read() or b"", e.headers.get("Content-Type", "application/json")
        except Exception as e:
            host = url.split("/")[2] if "//" in url else url
            raise UpstreamError(502, f"falha ao conectar em {host}: {getattr(e, 'reason', e)}",
                                "upstream_unreachable")

    def error_text(raw):
        try:
            err = json.loads(raw).get("error", raw)
            return err.get("message", str(err)) if isinstance(err, dict) else str(err)
        except Exception:
            return raw.decode("utf-8", "replace")[:500] if isinstance(raw, bytes) else str(raw)

    def bearer(key):
        return {"Authorization": "Bearer " + key} if key else {}

    def api_path(path):
        # Aceita /v1/chat/completions e /chat/completions (o base_url já termina em /v1)
        if path == "/v1" or path.startswith("/v1/") or path.startswith("/v1?"):
            return path[3:] or "/"
        return path

    def openai_models(base, key):
        status, raw, _ = http_call("GET", base + "/models", bearer(key))
        if status != 200:
            raise UpstreamError(status, error_text(raw), "upstream_error")
        try:
            items = json.loads(raw).get("data") or []
        except ValueError:
            raise UpstreamError(502, "resposta inválida ao listar modelos", "upstream_error")
        return [m.get("id") for m in items if isinstance(m, dict) and m.get("id")]

    # ----------------------------------------------- Anthropic (SDK oficial)
    ANTHROPIC_OFFICIAL = "https://api.anthropic.com"

    def anthropic_base(base):
        """URL base no formato do SDK (sem /v1): aceita '.../v1' e '.../v1/messages' colados por engano."""
        b = (base or "").strip().rstrip("/")
        for suffix in ("/v1/messages", "/v1"):
            if b.endswith(suffix):
                b = b[: -len(suffix)]
        return b or ANTHROPIC_OFFICIAL

    def is_official(base):
        return anthropic_base(base) == ANTHROPIC_OFFICIAL

    def anthropic_client(key, base):
        b = anthropic_base(base)
        kwargs = {"api_key": key, "timeout": float(TIMEOUT), "max_retries": 2}
        if b != ANTHROPIC_OFFICIAL:
            # Provedor compatível com a Anthropic (outra empresa ou proxy): uns leem
            # o cabeçalho x-api-key, outros "Authorization: Bearer". A chave vai nos dois.
            kwargs["base_url"] = b
            kwargs["auth_token"] = key
        return anthropic.Anthropic(**kwargs)

    def anthropic_error(e):
        """Converte erros do SDK da Anthropic em UpstreamError com mensagem clara."""
        detail = ""
        body = getattr(e, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                detail = err.get("message") or ""
        detail = detail or getattr(e, "message", "") or str(e)
        if isinstance(e, anthropic.AuthenticationError):
            return UpstreamError(401, "chave da API inválida: " + detail, "authentication_error")
        if isinstance(e, anthropic.PermissionDeniedError):
            return UpstreamError(403, "a chave não tem permissão para isso: " + detail, "permission_error")
        if isinstance(e, anthropic.NotFoundError):
            return UpstreamError(404, "modelo ou endpoint não encontrado: " + detail, "not_found_error")
        if isinstance(e, anthropic.RateLimitError):
            return UpstreamError(429, "limite de uso atingido (ou conta sem crédito): " + detail, "rate_limit_error")
        if isinstance(e, anthropic.APIStatusError):
            return UpstreamError(e.status_code, detail, "anthropic_error")
        if isinstance(e, anthropic.APIConnectionError):
            return UpstreamError(502, "falha ao conectar na Anthropic: " + detail, "upstream_unreachable")
        return UpstreamError(500, detail, "anthropic_error")

    def anthropic_models(key, base):
        client = anthropic_client(key, base)
        try:
            # Iterar a página percorre todas as páginas automaticamente
            return [m.id for m in client.models.list(limit=1000)]
        except anthropic.APIError as e:
            raise anthropic_error(e)

    def content_text(content):
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(p.get("text", "") for p in content
                           if isinstance(p, dict) and p.get("type") in ("text", "input_text"))
        return ""

    def data_url(url):
        """'data:<mime>;base64,<dados>' -> (mime, dados) ou None."""
        if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
            head, data = url.split(",", 1)
            return head[5:].split(";")[0], data
        return None

    def to_anthropic_blocks(content, role):
        """Conteúdo de uma mensagem no formato OpenAI (texto ou partes) -> blocos da Anthropic.
        Imagens e PDFs só entram em mensagens do usuário."""
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []
        blocks = []
        for p in content or []:
            if not isinstance(p, dict):
                continue
            t = p.get("type")
            if t in ("text", "input_text"):
                if p.get("text"):
                    blocks.append({"type": "text", "text": p["text"]})
            elif role != "user":
                continue
            elif t == "image_url":
                iu = p.get("image_url")
                url = iu.get("url") if isinstance(iu, dict) else iu
                du = data_url(url)
                if du:
                    blocks.append({"type": "image",
                                   "source": {"type": "base64", "media_type": du[0], "data": du[1]}})
                elif url:
                    blocks.append({"type": "image", "source": {"type": "url", "url": url}})
            elif t == "file":
                f = p.get("file") or {}
                du = data_url(f.get("file_data"))
                if du and du[0] == "application/pdf":
                    blocks.append({"type": "document", "title": f.get("filename") or "documento.pdf",
                                   "source": {"type": "base64", "media_type": "application/pdf", "data": du[1]}})
        return blocks

    def tool_result_text(content):
        if isinstance(content, str):
            return content
        return content_text(content) or json.dumps(content, ensure_ascii=False)

    def to_anthropic_tools(tools):
        """Ferramentas no formato OpenAI (function) -> formato Anthropic (input_schema)."""
        out = []
        for t in tools or []:
            f = t.get("function") if isinstance(t, dict) and t.get("type") == "function" else None
            if not f or not f.get("name"):
                continue
            schema = f.get("parameters") or {"type": "object", "properties": {}}
            out.append({"name": f["name"], "description": f.get("description") or "", "input_schema": schema})
        return out

    def to_anthropic_tool_choice(choice, parallel):
        # Só "auto" e "none": "any"/"tool" forçados dão 400 nos modelos Claude atuais
        tc = {"type": "none"} if choice == "none" else {"type": "auto"}
        if parallel is False and tc["type"] == "auto":
            tc["disable_parallel_tool_use"] = True
        return tc

    def to_anthropic_params(req, model):
        system_parts, messages = [], []

        def push(role, blocks):
            # Mensagens seguidas do mesmo papel viram uma só (os tool_result de uma
            # rodada precisam ir juntos, e na frente, na mesma mensagem do usuário)
            if messages and messages[-1]["role"] == role:
                prev = messages[-1]["content"]
                if isinstance(prev, str):
                    prev = [{"type": "text", "text": prev}]
                messages[-1]["content"] = prev + blocks
            else:
                messages.append({"role": role, "content": blocks})

        for m in req.get("messages") or []:
            role = m.get("role")
            if role in ("system", "developer"):
                text = content_text(m.get("content"))
                if text:
                    system_parts.append(text)
                continue
            if role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.get("tool_call_id") or "",
                         "content": tool_result_text(m.get("content"))}
                if m.get("tupi_is_error"):
                    block["is_error"] = True
                push("user", [block])
                continue
            if role == "assistant" and isinstance(m.get("tupi_raw"), list):
                # Conteúdo bruto devolvido por este router: volta EXATAMENTE como veio
                # (blocos de thinking com assinatura, tool_use, fallback...)
                push("assistant", m["tupi_raw"])
                continue
            if role not in ("user", "assistant"):
                role = "user"
            blocks = to_anthropic_blocks(m.get("content"), role)
            if role == "assistant":
                for tc in m.get("tool_calls") or []:
                    f = tc.get("function") or {}
                    try:
                        args = json.loads(f.get("arguments") or "{}")
                    except ValueError:
                        args = {}
                    blocks.append({"type": "tool_use", "id": tc.get("id") or "toolu_" + uuid.uuid4().hex[:20],
                                   "name": f.get("name") or "", "input": args if isinstance(args, dict) else {}})
            if blocks:
                push(role, blocks)
        # A conversa precisa começar por uma mensagem do usuário
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        if not messages:
            raise UpstreamError(400, "a conversa não tem nenhuma mensagem do usuário", "invalid_request")
        # Só texto vira string simples
        for msg in messages:
            c = msg["content"]
            if isinstance(c, list) and c and all(b.get("type") == "text" for b in c):
                msg["content"] = "\n\n".join(b["text"] for b in c)
        params = {
            "model": req.get("model") or model,
            "max_tokens": int(req.get("max_completion_tokens") or req.get("max_tokens") or ANTHROPIC_MAX_TOKENS),
            "messages": messages,
        }
        if system_parts:
            params["system"] = "\n\n".join(system_parts)
        tools = to_anthropic_tools(req.get("tools"))
        if tools:
            params["tools"] = tools
            params["tool_choice"] = to_anthropic_tool_choice(req.get("tool_choice"), req.get("parallel_tool_calls"))
        stop = req.get("stop")
        if stop:
            params["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
        # temperature/top_p/top_k NÃO são repassados: os modelos Claude atuais só
        # aceitam os valores padrão e recusam a requisição (400) se forem enviados.
        return params

    def anthropic_chat(key, base, model, req):
        if req.get("stream"):
            raise UpstreamError(400, "streaming ainda não é suportado com a Anthropic neste router",
                                "invalid_request")
        params = to_anthropic_params(req, model)
        client = anthropic_client(key, base)
        try:
            if params["model"] in ANTHROPIC_FALLBACK_MODELS and is_official(base):
                # Se um classificador de segurança recusar, a própria API tenta de
                # novo em outro modelo dentro da mesma chamada. Só na Anthropic
                # oficial: provedores compatíveis podem rejeitar o parâmetro.
                msg = client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"],
                    extra_body={"fallbacks": "default"},
                    **params,
                )
            else:
                msg = client.messages.create(**params)
        except anthropic.APIError as e:
            raise anthropic_error(e)

        data = msg.to_dict()
        stop_reason = data.get("stop_reason")
        raw = [b for b in data.get("content") or [] if isinstance(b, dict)]
        # Só blocos de texto viram resposta (blocos de thinking/fallback vão no tupi_raw)
        text = "".join(b.get("text", "") for b in raw if b.get("type") == "text")
        tool_calls = [{"id": b.get("id"), "type": "function",
                       "function": {"name": b.get("name"), "arguments": json.dumps(b.get("input") or {})}}
                      for b in raw if b.get("type") == "tool_use"]
        if stop_reason in ("refusal", "max_tokens"):
            # Recusa ou resposta cortada podem deixar um tool_use pela metade:
            # as ferramentas dessa rodada NUNCA são executadas
            tool_calls = []
        if stop_reason == "refusal":
            details = data.get("stop_details") or {}
            category = details.get("category") if isinstance(details, dict) else None
            text = text or ("A IA recusou responder a este pedido"
                            + (f" (categoria: {category})" if category else "") + ".")
        finish = {"end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
                  "refusal": "content_filter", "tool_use": "tool_calls"}.get(stop_reason, "stop")
        if finish == "tool_calls" and not tool_calls:
            finish = "stop"
        message = {"role": "assistant", "content": text or (None if tool_calls else "")}
        if tool_calls:
            message["tool_calls"] = tool_calls
        # Conteúdo bruto (thinking com assinatura, tool_use, fallback): o cliente devolve
        # isto sem alterar nas próximas chamadas, mantendo o histórico só-acrescentado.
        message["tupi_raw"] = raw
        usage = data.get("usage") or {}
        prompt_t, completion_t = usage.get("input_tokens") or 0, usage.get("output_tokens") or 0
        return {
            "id": data.get("id") or "chatcmpl-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": data.get("model") or params["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": prompt_t, "completion_tokens": completion_t,
                      "total_tokens": prompt_t + completion_t},
        }

    # ------------------------------------------------------- lista de modelos
    def list_models(provider, base, key):
        if provider == "anthropic":
            ids = anthropic_models(key, base)
        else:
            if provider == "ollama":
                base = base or OLLAMA + "/v1"
            elif not base:
                base = DEFAULT_BASE
            ids = openai_models(base.rstrip("/"), key)
            ids = [i for i in ids if not any(x in i.lower() for x in NON_CHAT)]
        if provider != "anthropic":
            ids = sorted(set(ids))
        preferred = [p for p in PREFERRED.get(provider, []) if p in ids]
        return preferred + [i for i in ids if i not in preferred]

    # ------------------------------------------------------------- servidor
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = UA

        def _send(self, status, body, ctype="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, obj):
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _error(self, status, message, kind):
            self._json(status, {"error": {"message": message, "type": kind}})

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                raise UpstreamError(400, "corpo da requisição não é JSON válido", "invalid_request")

        def _configured(self, conexao=None):
            cfg = load_config()
            if "_error" in cfg:
                raise UpstreamError(503, cfg["_error"], "config_error")
            if conexao and conexao != "principal":
                # Conexões extras (cada subagente do Pajé pode usar uma): "conexoes": {"nome": {...}}
                sub = (cfg.get("conexoes") or {}).get(conexao)
                if not isinstance(sub, dict):
                    raise UpstreamError(404, f"conexão de IA '{conexao}' não existe (Pajé → Harness → Conexões)",
                                        "not_found_error")
                up = resolve(normalize(sub))
                if up is None:
                    raise UpstreamError(503, f"conexão '{conexao}' sem chave da API", "not_configured")
                return up
            up = resolve(cfg)
            if up is None:
                raise UpstreamError(503, NOT_CONFIGURED, "not_configured")
            return up

        def _query(self, name):
            from urllib.parse import parse_qs, urlsplit
            return (parse_qs(urlsplit(self.path).query).get(name) or [None])[0]

        def _run(self, fn):
            try:
                fn()
            except UpstreamError as e:
                self._error(e.status, e.message, e.kind)
            except Exception as e:
                self._error(500, f"erro interno do router: {e}", "internal_error")

        def do_GET(self):
            self._run(self._get)

        def do_POST(self):
            self._run(self._post)

        def _get(self):
            if self.path in ("/", "/health"):
                cfg = load_config()
                up = None if "_error" in cfg else resolve(cfg)
                conns = [{"nome": "principal", "provider": up[0] if up else cfg.get("provider"),
                          "model": up[3] if up else None, "configured": up is not None}]
                for name, sub in sorted((cfg.get("conexoes") or {}).items()):
                    sup = resolve(normalize(sub)) if isinstance(sub, dict) else None
                    conns.append({"nome": name, "provider": sup[0] if sup else None,
                                  "model": sup[3] if sup else None, "configured": sup is not None})
                return self._json(200, {
                    "status": "ok",
                    "configured": up is not None,
                    "provider": up[0] if up else (cfg.get("provider") or None),
                    "model": up[3] if up else None,
                    "base_url": (anthropic_base(up[1]) if up[0] == "anthropic" else up[1]) if up else None,
                    "hint": None if up else (cfg.get("_error") or NOT_CONFIGURED),
                    "conexoes": conns,
                })
            provider, base, key, model = self._configured(self._query("conexao"))
            if api_path(self.path.split("?", 1)[0]).startswith("/models"):
                ids = list_models(provider, base, key)
                return self._json(200, {"object": "list", "data": [
                    {"id": i, "object": "model", "owned_by": provider} for i in ids]})
            if provider == "anthropic":
                raise UpstreamError(404, "endpoint não suportado com a Anthropic", "not_found_error")
            status, raw, ctype = http_call("GET", base + api_path(self.path), bearer(key))
            self._send(status, raw, ctype)

        def _post(self):
            if self.path == "/tupi/models":
                # Lista modelos de um provedor ANTES de salvar (usado pelos assistentes)
                b = self._body()
                provider = b.get("provider") or "openai"
                key = (b.get("api_key") or "").strip()
                if provider != "ollama" and not key:
                    raise UpstreamError(400, "informe a chave da API", "invalid_request")
                ids = list_models(provider, (b.get("base_url") or "").rstrip("/"), key)
                rec = next((p for p in PREFERRED.get(provider, []) if p in ids), None)
                return self._json(200, {"models": ids, "recommended": rec})

            body = self._body()
            conexao = body.pop("tupi_conexao", None) if isinstance(body, dict) else None
            provider, base, key, model = self._configured(conexao)
            if provider == "anthropic":
                if api_path(self.path) != "/chat/completions":
                    raise UpstreamError(404, "endpoint não suportado com a Anthropic", "not_found_error")
                return self._json(200, anthropic_chat(key, base, model, body))
            if isinstance(body, dict) and not body.get("model"):
                body["model"] = model
            status, raw, ctype = http_call("POST", base + api_path(self.path), bearer(key), body)
            self._send(status, raw, ctype)

        def log_message(self, fmt, *args):
            # Só a linha da requisição; a chave da API nunca vai para o log
            sys.stderr.write("[AI-Router] %s\n" % (fmt % args))

    class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    if __name__ == "__main__":
        print(f"[AI-Router] listening on {HOST}:{PORT} (config: {CONFIG_PATH})", flush=True)
        Server((HOST, PORT), Handler).serve_forever()
  '';

  askScript = pkgs.writeText "tupi-ask.py" ''
    #!/usr/bin/env python3
    """tupi-ask — conversa com a IA do TUPIOS pelo terminal."""
    import json, os, sys, urllib.request, urllib.error

    URL = os.environ.get("TUPI_AI_URL", "${routerUrl}").rstrip("/")
    SYSTEM = ("Você é o Pajé, a IA integrada ao TUPIOS, um sistema operacional baseado em NixOS. "
              "Responda em português do Brasil, de forma clara e objetiva.")

    class AIError(Exception):
        pass

    def request(method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(URL + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=320) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                err = json.loads(raw).get("error", raw)
                msg = err.get("message", err) if isinstance(err, dict) else err
            except ValueError:
                msg = raw
            raise AIError(f"Erro da IA (HTTP {e.code}): {msg}")
        except urllib.error.URLError as e:
            raise AIError(f"Não foi possível falar com o TUPIOS AI Router em {URL}: {e.reason}\n"
                          "Verifique com: systemctl status tupi-ai-router")

    def chat(messages):
        data = request("POST", "/v1/chat/completions", {"messages": messages})
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise AIError("Resposta inesperada da IA: " + json.dumps(data)[:500])

    def main():
        args = sys.argv[1:]
        if args and args[0] in ("-h", "--help"):
            print("Uso: tupi-ask \"pergunta\"   responde e sai\n"
                  "     tupi-ask              chat interativo\n"
                  "     echo texto | tupi-ask lê a pergunta da entrada padrão\n"
                  "     tupi-ask --status     estado do AI Router")
            return 0
        try:
            if args and args[0] == "--status":
                print(json.dumps(request("GET", "/health"), ensure_ascii=False, indent=2))
                return 0
            messages = [{"role": "system", "content": SYSTEM}]
            question = " ".join(args) if args else (None if sys.stdin.isatty() else sys.stdin.read().strip())
            if question:
                messages.append({"role": "user", "content": question})
                print(chat(messages))
                return 0
            status = request("GET", "/health")
        except AIError as e:
            print(e, file=sys.stderr)
            return 1

        model = status.get("model") or "?"
        print(f"🤖 Pajé — chat ({status.get('provider') or '?'} / {model})")
        print("   Digite sua pergunta e Enter. Para sair: 'sair' ou Ctrl+D.")
        if not status.get("configured"):
            print("⚠️  " + (status.get("hint") or "IA não configurada."))
        while True:
            try:
                q = input("\nvocê> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not q:
                continue
            if q.lower() in ("sair", "exit", "quit"):
                return 0
            messages.append({"role": "user", "content": q})
            try:
                answer = chat(messages)
            except AIError as e:
                messages.pop()
                print(e, file=sys.stderr)
                continue
            messages.append({"role": "assistant", "content": answer})
            print("\ntupi> " + answer)

    if __name__ == "__main__":
        sys.exit(main())
  '';

  configScript = pkgs.writeText "tupi-ai-config.py" ''
    #!/usr/bin/env python3
    """tupi-ai-config — escolhe provedor, chave e modelo da IA do TUPIOS."""
    import getpass, grp, json, os, sys, urllib.request, urllib.error

    CONFIG_PATH = os.environ.get("TUPI_AI_CONFIG", "${cfg.configFile}")
    ROUTER = os.environ.get("TUPI_AI_URL", "${routerUrl}").rstrip("/")
    GROUP = "tupi-ia"

    # id, nome, provider, base_url, onde criar a chave, precisa de chave, pede URL
    PRESETS = [
        ("openai", "OpenAI (ChatGPT)", "openai", "https://api.openai.com/v1",
         "https://platform.openai.com/api-keys", True, False),
        ("anthropic", "Anthropic (Claude) — oficial", "anthropic", "https://api.anthropic.com",
         "https://platform.claude.com  (menu API Keys)", True, False),
        ("anthropic-compat", "Compatível com Anthropic (outra empresa/proxy — informar URL)", "anthropic", "",
         "", True, True),
        ("groq", "Groq (tem plano gratuito)", "openai", "https://api.groq.com/openai/v1",
         "https://console.groq.com/keys", True, False),
        ("openrouter", "OpenRouter (centenas de modelos)", "openai", "https://openrouter.ai/api/v1",
         "https://openrouter.ai/keys", True, False),
        ("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1",
         "https://platform.deepseek.com/api_keys", True, False),
        ("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai",
         "https://aistudio.google.com/apikey", True, False),
        ("ollama", "Ollama (IA local, sem chave)", "ollama", "http://127.0.0.1:11434/v1", "", False, False),
        ("custom", "Compatível com OpenAI (outra empresa/servidor — informar URL)", "openai", "",
         "", True, True),
    ]
    BY_ID = {p[0]: p for p in PRESETS}
    URL_HINT = {
        "anthropic-compat": "URL base da API compatível com Anthropic (ex.: https://api.exemplo.com/anthropic): ",
        "custom": "URL base da API compatível com OpenAI (ex.: https://meu-servidor/v1): ",
    }
    # Provedor sem a rota de listagem de modelos: o nome é digitado à mão
    LIST_UNSUPPORTED = (404, 405, 501)
    EXIT_NO_LIST = 3

    def die(msg, code=1):
        print(msg, file=sys.stderr)
        sys.exit(code)

    class RouterDown(RuntimeError):
        """O serviço tupi-ai-router não respondeu (não adianta trocar a chave)."""

    class RouterHTTPError(RuntimeError):
        def __init__(self, status, msg):
            super().__init__(f"(HTTP {status}) {msg}")
            self.status = status

    def router(method, path, payload=None, timeout=60):
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
            raise RouterHTTPError(e.code, msg)
        except urllib.error.URLError as e:
            raise RouterDown(f"o TUPIOS AI Router não respondeu ({e.reason}). "
                             "Verifique: systemctl status tupi-ai-router")

    def preset_or_die(pid):
        if pid not in BY_ID:
            die(f"provedor desconhecido: {pid}. Opções: " + ", ".join(BY_ID))
        return BY_ID[pid]

    def fetch_models(pid, key, base_url=None):
        """(lista de modelos, recomendado ou None). Lista vazia = o provedor não informa os modelos."""
        provider, base = preset_or_die(pid)[2:4]
        try:
            r = router("POST", "/tupi/models",
                       {"provider": provider, "base_url": base_url or base, "api_key": key})
        except RouterHTTPError as e:
            if e.status in LIST_UNSUPPORTED:
                return [], None
            raise
        return r.get("models") or [], r.get("recommended")

    def write_config(pid, key, model, base_url=None):
        provider, base = preset_or_die(pid)[2:4]
        data = {"provider": provider, "preset": pid, "base_url": base_url or base,
                "api_key": key, "model": model}
        text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        try:
            if os.path.exists(CONFIG_PATH):
                # Grava no próprio arquivo: mantém dono e permissões (0660 root:tupi-ia)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write(text)
            else:
                os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    f.write(text)
                if os.geteuid() == 0:
                    os.chown(CONFIG_PATH, 0, grp.getgrnam(GROUP).gr_gid)
                os.chmod(CONFIG_PATH, 0o660)
        except PermissionError:
            die(f"Sem permissão para gravar {CONFIG_PATH}.\n"
                f"Seu usuário precisa estar no grupo '{GROUP}' (ou rode com sudo).")

    def test_chat():
        data = router("POST", "/v1/chat/completions",
                      {"messages": [{"role": "user", "content": "Responda apenas: Pajé OK"}]},
                      timeout=320)
        return (data["choices"][0]["message"]["content"] or "").strip()

    def is_configured():
        try:
            return bool(router("GET", "/health", timeout=10).get("configured"))
        except RuntimeError:
            return False

    # --------------------------------------------------------- terminal
    def ask(prompt, default=""):
        try:
            v = input(prompt).strip()
        except EOFError:
            v = ""
        return v or default

    def read_secret(prompt):
        # Num terminal a chave não aparece; com entrada por pipe/script, lê da entrada padrão
        # (getpass abriria /dev/tty e ficaria esperando digitação).
        if sys.stdin.isatty():
            return getpass.getpass(prompt).strip()
        print(prompt, end="", flush=True)
        return sys.stdin.readline().strip()

    def choose_model(models, recommended=None):
        shown = models
        while True:
            print("")
            for i, m in enumerate(shown[:40], 1):
                print(f"  {i:2d}) {m}" + ("   ← recomendado" if m == recommended else ""))
            if len(shown) > 40:
                print(f"  ... e mais {len(shown) - 40}. Digite parte do nome para filtrar.")
            v = ask("Número do modelo [1], ou parte do nome para filtrar: ", "1")
            if v.isdigit() and 1 <= int(v) <= min(len(shown), 40):
                return shown[int(v) - 1]
            if v in models:
                return v
            filtered = [m for m in models if v.lower() in m.lower()]
            if filtered:
                shown = filtered
            else:
                print(f"Nenhum modelo contém '{v}'.")
                shown = models

    def setup():
        print("🤖 TUPIOS — Configuração da IA")
        print("=" * 30)
        for i, p in enumerate(PRESETS, 1):
            print(f"  {i}) {p[1]}")
        v = ask("Escolha o provedor [1]: ", "1")
        if not v.isdigit() or not 1 <= int(v) <= len(PRESETS):
            die("Opção inválida.")
        pid, name, provider, base, keys_url, needs_key, needs_url = PRESETS[int(v) - 1]
        base_url = None
        if needs_url:
            base_url = ask(URL_HINT.get(pid, "URL base da API: "))
            if not base_url:
                die("URL vazia. Nada foi alterado.")
        for attempt in range(3):
            key = ""
            if needs_key:
                if keys_url:
                    print(f"\nOnde criar a chave: {keys_url}")
                key = read_secret("Chave da API (não aparece ao digitar): ")
                if not key:
                    die("Chave vazia. Nada foi alterado.")
            print("\n🔎 Buscando a lista de modelos...")
            try:
                models, recommended = fetch_models(pid, key, base_url)
                break
            except RouterDown as e:
                die(f"❌ {e}")
            except RuntimeError as e:
                print(f"❌ Não foi possível listar os modelos: {e}")
                if not needs_key or attempt == 2:
                    die("Nada foi alterado.")
                print("Tente a chave de novo.")
        if models:
            model = choose_model(models, recommended)
        else:
            print("ℹ️  Este provedor não informa a lista de modelos.")
            model = ask("Digite o nome exato do modelo (veja na documentação do provedor): ")
            if not model:
                die("Nenhum modelo informado. Nada foi alterado.")
        write_config(pid, key, model, base_url)
        print(f"\n✅ Configuração salva: {name} / {model}")
        print("🔎 Testando a IA...")
        try:
            answer = test_chat()
        except RuntimeError as e:
            print(f"⚠️  O teste falhou: {e}")
            print("   Rode 'tupi-ai-setup' de novo para trocar a chave ou o modelo.")
            return 2
        print(f"✅ A IA respondeu: {answer}")
        print('   Use: tupi-ask "sua pergunta"   (ou só tupi-ask para conversar)')
        return 0

    # ------------------------------------------------------------- CLI
    def main():
        args = sys.argv[1:]
        cmd = args[0] if args else "setup"
        base_url = None
        if "--base-url" in args:
            i = args.index("--base-url")
            base_url = args[i + 1] if i + 1 < len(args) else None
            del args[i:i + 2]
        key = os.environ.get("TUPI_AI_KEY", "").strip()
        try:
            if cmd == "setup":
                return setup()
            if cmd == "presets":
                # id, nome, precisa de chave, onde criar a chave, URL base, pede URL
                for pid, name, _, base, keys_url, needs_key, needs_url in PRESETS:
                    print("\t".join([pid, name, "1" if needs_key else "0", keys_url, base,
                                     "1" if needs_url else "0"]))
                return 0
            if cmd == "models" and len(args) >= 2:
                # Um modelo por linha; o recomendado vem marcado com "<TAB>*".
                # Saída 3 = o provedor não informa a lista (digitar o nome à mão).
                models, recommended = fetch_models(args[1], key, base_url)
                if not models:
                    print("este provedor não informa a lista de modelos", file=sys.stderr)
                    return EXIT_NO_LIST
                for m in models:
                    print(m + ("\t*" if m == recommended else ""))
                return 0
            if cmd == "save" and len(args) >= 3:
                write_config(args[1], key, args[2], base_url)
                return 0
            if cmd == "test":
                print(test_chat())
                return 0
            if cmd == "is-configured":
                return 0 if is_configured() else 1
            if cmd == "status":
                print(json.dumps(router("GET", "/health", timeout=10), ensure_ascii=False, indent=2))
                return 0
        except RuntimeError as e:
            die(str(e))
        die("Uso: tupi-ai-config [setup | presets | models PRESET | save PRESET MODELO | test | status | is-configured]\n"
            "     (a chave vai na variável de ambiente TUPI_AI_KEY; opcional: --base-url URL)")

    if __name__ == "__main__":
        sys.exit(main())
  '';

  tupiAsk = pkgs.writeShellScriptBin "tupi-ask" ''
    exec ${pkgs.python3}/bin/python3 ${askScript} "$@"
  '';

  tupiAiConfig = pkgs.writeShellScriptBin "tupi-ai-config" ''
    exec ${pkgs.python3}/bin/python3 ${configScript} "$@"
  '';

  tupiAiSetup = pkgs.writeShellScriptBin "tupi-ai-setup" ''
    exec ${tupiAiConfig}/bin/tupi-ai-config setup "$@"
  '';

  # ---------------------------------------------------------------- GUI
  tupiAiChat = pkgs.writeShellScriptBin "tupi-ai-chat" ''
    # Abre o chat da IA numa janela de terminal (configura antes, se preciso)
    if [ "''${1:-}" != "--no-check" ] && ! ${tupiAiConfig}/bin/tupi-ai-config is-configured; then
      ${tupiAiConfigGui}/bin/tupi-ai-config-gui --from-chat || exit 0
      ${tupiAiConfig}/bin/tupi-ai-config is-configured || exit 0
    fi
    if command -v xfce4-terminal >/dev/null 2>&1; then
      exec xfce4-terminal --title="Pajé" --hide-menubar --geometry=110x32 -x ${tupiAsk}/bin/tupi-ask
    elif command -v foot >/dev/null 2>&1; then
      exec foot --title "Pajé" ${tupiAsk}/bin/tupi-ask
    else
      exec xterm -T "Pajé" -e ${tupiAsk}/bin/tupi-ask
    fi
  '';

  tupiAiConfigGui = pkgs.writeShellScriptBin "tupi-ai-config-gui" ''
    # Assistente gráfico (janelas) para escolher provedor, chave e modelo da IA
    set -u
    Z=${pkgs.zenity}/bin/zenity
    CFG=${tupiAiConfig}/bin/tupi-ai-config
    T="Configurar IA — TUPIOS"
    FROM_CHAT=0
    [ "''${1:-}" = "--from-chat" ] && FROM_CHAT=1

    WORK=$(mktemp -d)
    trap 'rm -rf "$WORK"' EXIT

    PRESETS=$($CFG presets) || exit 1

    # 1) Provedor
    ARGS=()
    FIRST=1
    while IFS=$'\t' read -r id name needs keys base needs_url; do
      if [ "$FIRST" = 1 ]; then ARGS+=(TRUE "$id" "$name"); FIRST=0; else ARGS+=(FALSE "$id" "$name"); fi
    done <<< "$PRESETS"
    PRESET=$($Z --list --radiolist --title="$T" --width=640 --height=540 \
      --text="Escolha o provedor de IA:" \
      --column="" --column="id" --column="Provedor" --hide-column=2 --print-column=2 \
      "''${ARGS[@]}") || exit 1
    [ -n "$PRESET" ] || exit 1
    LINE=$(awk -F'\t' -v p="$PRESET" '$1 == p' <<< "$PRESETS")
    IFS=$'\t' read -r _ NAME NEEDS KEYS BASE NEEDS_URL <<< "$LINE"

    BASE_ARG=()
    if [ "$NEEDS_URL" = 1 ]; then
      if [ "$PRESET" = anthropic-compat ]; then
        UTXT="URL base da API compatível com Anthropic"$'\n'"(ex.: https://api.exemplo.com/anthropic — sem /v1/messages no fim):"
      else
        UTXT="URL base da API compatível com OpenAI (ex.: https://meu-servidor/v1):"
      fi
      URL=$($Z --entry --title="$T" --width=600 --text="$UTXT") || exit 1
      [ -n "$URL" ] || exit 1
      BASE_ARG=(--base-url "$URL")
    fi

    # 2) Chave da API + 3) lista de modelos (repete se a chave estiver errada)
    KEY=""
    MANUAL=0
    while :; do
      if [ "$NEEDS" = 1 ]; then
        TXT="Cole a chave da API de $NAME (Ctrl+V)."
        [ -n "$KEYS" ] && TXT="$TXT"$'\n\n'"Onde criar a chave: $KEYS"$'\n'"(há atalhos na barra de favoritos do Firefox)"
        KEY=$($Z --entry --hide-text --title="$T" --width=600 --text="$TXT") || exit 1
        [ -n "$KEY" ] || continue
      fi
      ( TUPI_AI_KEY="$KEY" "$CFG" models "$PRESET" "''${BASE_ARG[@]}" >"$WORK/models" 2>"$WORK/err"
        echo $? >"$WORK/rc" ) | $Z --progress --pulsate --auto-close --no-cancel --title="$T" \
          --width=420 --text="Buscando a lista de modelos de $NAME..."
      RC=$(cat "$WORK/rc" 2>/dev/null)
      if [ "$RC" = 0 ] && [ -s "$WORK/models" ]; then
        break
      fi
      # 3 = o provedor não tem a rota de listagem de modelos: digitar o nome
      if [ "$RC" = 3 ]; then
        MANUAL=1
        break
      fi
      $Z --error --no-markup --title="$T" --width=560 \
        --text="Não foi possível obter a lista de modelos:"$'\n\n'"$(head -c 700 "$WORK/err")"
      [ "$NEEDS" = 1 ] || exit 1
    done

    # 4) Modelo (lista vinda do provedor; o recomendado já vem marcado)
    if [ "$MANUAL" = 1 ]; then
      MODEL=$($Z --entry --title="$T" --width=600 \
        --text="$NAME não informa a lista de modelos."$'\n'"Digite o nome exato do modelo (veja na documentação do provedor):") || exit 1
      [ -n "$MODEL" ] || exit 1
    else
    ROWS=()
    FIRST_MODEL=""
    HAS_REC=0
    while IFS=$'\t' read -r id star; do
      [ -n "$id" ] || continue
      [ -n "$FIRST_MODEL" ] || FIRST_MODEL="$id"
      if [ "$star" = "*" ]; then ROWS+=(TRUE "$id" "recomendado"); HAS_REC=1; else ROWS+=(FALSE "$id" ""); fi
    done < "$WORK/models"
    # Sem recomendado conhecido: marca o primeiro da lista
    [ "$HAS_REC" = 1 ] || ROWS[0]=TRUE
    MODEL=$($Z --list --radiolist --title="$T" --width=600 --height=560 \
      --text="Escolha o modelo de $NAME (lista vinda do provedor):" \
      --column="" --column="Modelo" --column="" --print-column=2 \
      "''${ROWS[@]}") || exit 1
    MODEL=''${MODEL%%|*}
    [ -n "$MODEL" ] || MODEL="$FIRST_MODEL"
    fi

    # 5) Salvar + testar
    if ! OUT=$(TUPI_AI_KEY="$KEY" "$CFG" save "$PRESET" "$MODEL" "''${BASE_ARG[@]}" 2>&1); then
      $Z --error --no-markup --title="$T" --width=560 --text="$OUT"
      exit 1
    fi
    ( "$CFG" test >"$WORK/test" 2>&1; echo $? >"$WORK/trc" ) | $Z --progress --pulsate --auto-close \
        --no-cancel --title="$T" --width=420 --text="Testando $MODEL..."
    if [ "$(cat "$WORK/trc" 2>/dev/null)" != 0 ]; then
      $Z --error --no-markup --title="$T" --width=560 \
        --text="A configuração foi salva, mas o teste falhou:"$'\n\n'"$(head -c 700 "$WORK/test")"$'\n\n'"Confira a chave e o modelo e tente de novo."
      exit 1
    fi
    MSG="✅ IA configurada!"$'\n\n'"Provedor: $NAME"$'\n'"Modelo: $MODEL"$'\n'"Resposta do teste: $(head -c 200 "$WORK/test")"
    if [ "$FROM_CHAT" = 1 ]; then
      $Z --info --no-markup --title="$T" --width=480 --text="$MSG"
      exit 0
    fi
    if $Z --question --no-markup --title="$T" --width=480 --ok-label="Abrir chat" --cancel-label="Fechar" \
        --text="$MSG"$'\n\n'"Abrir o chat com a IA agora?"; then
      # Pelo nome (PATH): tupi-ai-chat já referencia este script; o caminho no
      # store criaria uma dependência circular entre as duas derivações.
      setsid tupi-ai-chat --no-check >/dev/null 2>&1 &
    fi
    exit 0
  '';

  # Abre o assistente sozinho no primeiro login, se a IA ainda não estiver configurada
  tupiAiFirstrun = pkgs.writeShellScriptBin "tupi-ai-firstrun" ''
    sleep 4
    ${tupiAiConfig}/bin/tupi-ai-config is-configured || exec ${tupiAiConfigGui}/bin/tupi-ai-config-gui
  '';

  tupiIcon = pkgs.writeTextDir "share/icons/hicolor/scalable/apps/tupios-ia.svg" ''
    <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
      <rect x="2" y="2" width="60" height="60" rx="14" fill="#14361f"/>
      <circle cx="32" cy="32" r="22" fill="none" stroke="#52EA23" stroke-width="4"/>
      <text x="32" y="40" font-family="DejaVu Sans, sans-serif" font-size="22" font-weight="bold"
            text-anchor="middle" fill="#52EA23">IA</text>
    </svg>
  '';

  chatDesktop = pkgs.makeDesktopItem {
    name = "tupi-ai-chat";
    desktopName = "Pajé (terminal)";
    comment = "Converse com a IA do TUPIOS pelo terminal";
    exec = "${tupiAiChat}/bin/tupi-ai-chat";
    icon = "tupios-ia";
    categories = [ "Utility" ];
  };

  configDesktop = pkgs.makeDesktopItem {
    name = "tupi-ai-config";
    desktopName = "Configurar IA";
    comment = "Escolher provedor (OpenAI, Claude, Groq...), chave da API e modelo";
    exec = "${tupiAiConfigGui}/bin/tupi-ai-config-gui";
    icon = "preferences-system";
    categories = [ "Settings" "Utility" ];
  };
in
{
  options.tupi.aiRouter = {
    enable = lib.mkEnableOption "TUPIOS AI Router (proxy OpenAI-compatible + tupi-ask)";

    listenAddress = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Endereço de escuta. 127.0.0.1 = só a própria máquina.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8080;
      description = "Porta HTTP do router (endpoints /v1/chat/completions, /v1/models, /health).";
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Abrir a porta do router no firewall.";
    };

    configFile = lib.mkOption {
      type = lib.types.str;
      default = "/etc/tupi/ai-config.json";
      description = ''
        Arquivo com provedor, chave da API e modelo (gerado por tupi-ai-setup / Configurar IA).
        Fica 0660 root:tupi-ia: membros do grupo tupi-ia configuram a IA sem sudo.
      '';
    };

    defaultBackend = lib.mkOption {
      type = lib.types.enum [ "remote" "local" ];
      default = "remote";
      description = "Backend usado quando o arquivo de configuração não define um.";
    };

    defaultBaseUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://api.openai.com/v1";
      description = "URL base padrão da API remota compatível com OpenAI.";
    };

    defaultModel = lib.mkOption {
      type = lib.types.str;
      default = "gpt-4o-mini";
      description = "Modelo padrão quando o provedor é compatível com OpenAI e nenhum modelo foi escolhido.";
    };

    defaultLocalModel = lib.mkOption {
      type = lib.types.str;
      default = "llama3.2";
      description = "Modelo padrão do Ollama.";
    };

    ollamaUrl = lib.mkOption {
      type = lib.types.str;
      default = "http://127.0.0.1:11434";
      description = "URL do Ollama para o backend local.";
    };

    desktopIntegration = lib.mkOption {
      type = lib.types.bool;
      default = config.services.xserver.enable;
      defaultText = lib.literalExpression "config.services.xserver.enable";
      description = "Assistente gráfico 'Configurar IA', atalho 'Pajé (terminal)' e abertura automática no 1º login.";
    };
  };

  config = lib.mkIf cfg.enable (lib.mkMerge [
    {
      users.groups.tupi-ia = { };

      # O arquivo de configuração existe desde o boot com dono/permissões certos,
      # assim usuários do grupo tupi-ia gravam nele sem precisar de sudo.
      systemd.tmpfiles.rules = [
        "d ${dirOf cfg.configFile} 0755 root root -"
        "f ${cfg.configFile} 0660 root tupi-ia -"
      ];

      systemd.services.tupi-ai-router = {
        description = "TUPIOS AI Router (OpenAI-compatible proxy)";
        wantedBy = [ "multi-user.target" ];
        # Sem network-online: o router não precisa de rede para subir, e esperar
        # por ela atrasaria o boot em máquinas sem cabo. A rede é usada por requisição.
        after = [ "network.target" "ollama.service" "systemd-tmpfiles-setup.service" ];
        environment = {
          TUPI_AI_CONFIG = cfg.configFile;
          ROUTER_HOST = cfg.listenAddress;
          ROUTER_PORT = toString cfg.port;
          OLLAMA_HOST = cfg.ollamaUrl;
          TUPI_DEFAULT_BACKEND = cfg.defaultBackend;
          TUPI_DEFAULT_MODEL = cfg.defaultModel;
          TUPI_DEFAULT_LOCAL_MODEL = cfg.defaultLocalModel;
          TUPI_DEFAULT_BASE_URL = cfg.defaultBaseUrl;
          # Serviços systemd não herdam SSL_CERT_FILE; sem isto o HTTPS falha
          SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
        };
        serviceConfig = {
          Type = "simple";
          ExecStart = "${routerPython}/bin/python3 -u ${routerScript}";
          Restart = "always";
          RestartSec = "3s";
          # Usuário efêmero sem privilégios; o grupo tupi-ia dá leitura à chave
          DynamicUser = true;
          SupplementaryGroups = [ "tupi-ia" ];
          NoNewPrivileges = true;
          PrivateTmp = true;
          PrivateDevices = true;
          ProtectSystem = "strict";
          ProtectHome = true;
          ProtectKernelTunables = true;
          ProtectKernelModules = true;
          ProtectControlGroups = true;
          RestrictAddressFamilies = [ "AF_INET" "AF_INET6" "AF_UNIX" ];
          SystemCallFilter = [ "@system-service" ];
        };
      };

      networking.firewall.allowedTCPPorts = lib.optional cfg.openFirewall cfg.port;

      environment.systemPackages = [ tupiAsk tupiAiConfig tupiAiSetup ];
    }

    (lib.mkIf cfg.desktopIntegration {
      environment.systemPackages = [ tupiAiChat tupiAiConfigGui chatDesktop configDesktop tupiIcon ];
      environment.etc."xdg/autostart/tupi-ai-firstrun.desktop".text = ''
        [Desktop Entry]
        Type=Application
        Name=Pajé — primeira configuração
        Exec=${tupiAiFirstrun}/bin/tupi-ai-firstrun
        NoDisplay=true
        X-GNOME-Autostart-enabled=true
      '';
    })
  ]);
}
