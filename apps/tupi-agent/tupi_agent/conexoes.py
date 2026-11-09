"""Conexões de IA do Pajé e dos subagentes (em /etc/tupi/ai-config.json, campo "conexoes").

O arquivo é root:tupi-ia 0660: o usuário (no grupo tupi-ia) edita pelo app ou pelo terminal; a IA e os
subagentes NÃO conseguem ler (o executor esconde o arquivo das unidades da IA). As chaves nunca vão para as
almas (.soul.md) nem para o harness.

Cada subagente pode ter um PROVEDOR PRÓPRIO: uma conexão "ag-<id>" só dele (URL + chave + modelo).
"""
import json
import os
import re

AI_CONFIG = os.environ.get("TUPI_AI_CONFIG", "/etc/tupi/ai-config.json")
# Os mesmos provedores do "Configurar IA"
PRESETS = [
    {"id": "openai", "nome": "OpenAI (ChatGPT)", "provider": "openai", "base_url": "https://api.openai.com/v1"},
    {"id": "anthropic", "nome": "Anthropic (Claude) — oficial", "provider": "anthropic", "base_url": ""},
    {"id": "anthropic-compat", "nome": "Compatível com Anthropic (informar URL)", "provider": "anthropic",
     "base_url": "", "url": True},
    {"id": "groq", "nome": "Groq", "provider": "openai", "base_url": "https://api.groq.com/openai/v1"},
    {"id": "openrouter", "nome": "OpenRouter", "provider": "openai", "base_url": "https://openrouter.ai/api/v1"},
    {"id": "deepseek", "nome": "DeepSeek", "provider": "openai", "base_url": "https://api.deepseek.com/v1"},
    {"id": "gemini", "nome": "Google Gemini", "provider": "openai",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    {"id": "ollama", "nome": "Ollama (IA local, sem chave)", "provider": "ollama",
     "base_url": "http://127.0.0.1:11434/v1", "semchave": True},
    {"id": "custom", "nome": "Compatível com OpenAI (informar URL)", "provider": "openai", "base_url": "", "url": True},
]
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,29}$")
AGENT_PREFIX = "ag-"


class ConfigError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def preset(pid):
    p = next((x for x in PRESETS if x["id"] == pid), None)
    if not p:
        raise ConfigError(400, "escolha um provedor")
    return p


def preset_params(body, need_key=True):
    """{preset, base_url, api_key} -> (provider, base_url, chave). Valida URL e chave."""
    p = preset((body or {}).get("preset"))
    base = (body.get("base_url") or p["base_url"] or "").strip().rstrip("/")
    if (p.get("url") or body.get("base_url")) and not base.startswith(("http://", "https://")):
        raise ConfigError(400, "informe a URL base da API (http:// ou https://)")
    key = (body.get("api_key") or "").strip()
    if need_key and not p.get("semchave") and not key:
        raise ConfigError(400, "informe a chave da API")
    return p["provider"], base, key


def read_ai_config():
    try:
        with open(AI_CONFIG, encoding="utf-8") as f:
            text = f.read()
        return json.loads(text) if text.strip() else {}
    except PermissionError:
        raise ConfigError(403, f"sem permissão para ler {AI_CONFIG} (o usuário precisa estar no grupo tupi-ia)")
    except FileNotFoundError:
        return {}
    except ValueError:
        raise ConfigError(400, f"{AI_CONFIG} inválido")


def edit_ai_config(fn):
    """Altera o arquivo NO LUGAR (mantém dono e permissões 0660 root:tupi-ia)."""
    try:
        with open(AI_CONFIG, "r+", encoding="utf-8") as f:
            text = f.read()
            data = json.loads(text) if text.strip() else {}
            result = fn(data)
            f.seek(0)
            f.truncate()
            json.dump(data, f, ensure_ascii=False, indent=1)
            return result
    except PermissionError:
        raise ConfigError(403, f"sem permissão para gravar {AI_CONFIG} (o usuário precisa estar no grupo tupi-ia)")
    except FileNotFoundError:
        raise ConfigError(400, "configure a IA principal primeiro (Configurar IA)")
    except ValueError:
        raise ConfigError(400, f"{AI_CONFIG} inválido")


# ------------------------------------------------- provedor próprio de um subagente
def agent_conn_name(agent_id):
    return AGENT_PREFIX + re.sub(r"[^a-z0-9_-]", "-", str(agent_id).lower())[:27]


def set_agent_provider(agent_id, body):
    """Grava (ou troca) a conexão só deste subagente. Chave em branco mantém a salva, desde que o provedor e a
    URL não mudem (nunca manda a chave antiga para outro endereço). -> (nome_da_conexão, modelo)"""
    provider, base, key = preset_params(body, need_key=False)
    p = preset(body.get("preset"))
    model = str(body.get("model") or "").strip()[:200]
    name = agent_conn_name(agent_id)

    def fn(d):
        conns = d.setdefault("conexoes", {})
        old = conns.get(name) or {}
        k = key
        if not k and old.get("provider") == provider and (old.get("base_url") or "") == base:
            k = old.get("api_key") or ""
        if not p.get("semchave") and not k:
            raise ConfigError(400, "informe a chave da API deste provedor")
        conns[name] = {"provider": provider, "base_url": base, "api_key": k, "model": model, "preset": p["id"],
                       "agente": str(agent_id)}
    edit_ai_config(fn)
    return name, model


def remove_agent_provider(agent_id):
    name = agent_conn_name(agent_id)
    try:
        return edit_ai_config(lambda d: (d.get("conexoes") or {}).pop(name, None) is not None)
    except ConfigError:
        return False


def agent_provider_public(agent_id):
    """O provedor próprio do agente SEM a chave (para mostrar no formulário)."""
    try:
        c = (read_ai_config().get("conexoes") or {}).get(agent_conn_name(agent_id))
    except ConfigError:
        return None
    if not c:
        return None
    return {"conexao": agent_conn_name(agent_id), "preset": c.get("preset") or ("custom" if c.get("provider") ==
                                                                                 "openai" else c.get("provider")),
            "base_url": c.get("base_url") or "", "model": c.get("model") or "", "tem_chave": bool(c.get("api_key"))}
