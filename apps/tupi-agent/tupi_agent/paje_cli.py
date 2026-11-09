"""paje — o harness do Pajé pelo terminal.

  paje                                   estado do motor (trabalhos rodando/na fila)
  paje agentes                           lista os subagentes
  paje agente ID                         mostra a alma (.soul.md) de um subagente
  paje novo NOME --papel "…" [--base pesquisador|analista|escritor] [--ferramentas a,b]
                 [--conexao NOME] [--modelo ID] [--alma ARQUIVO.md]
                 [--provedor custom|openai|anthropic|anthropic-compat|groq|openrouter|deepseek|gemini|ollama
                  --url URL --chave CHAVE]   (provedor próprio: a chave fica protegida, nunca na alma)
  paje provedor ID --provedor P [--url URL] [--chave CHAVE] [--modelo M]  |  paje provedor ID --remover
  paje modelos ID                        modelos disponíveis no provedor do subagente
  paje modelos --provedor P --url URL [--chave CHAVE]   (antes de salvar)
  paje apagar ID
  paje trabalho AGENTE "instruções" [--titulo "…"]
  paje trabalhos                         quadro de trabalhos
  paje ver ID                            passos e relatório de um trabalho
  paje esperar ID [ID…] [--segundos N]
  paje cancelar ID
  paje harness                           constituição, ferramentas, conexões, limites e arquivos
"""
import getpass
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from . import conexoes, souls
from .tools import paje_op

ROUTER = os.environ.get("TUPI_AI_URL", "http://127.0.0.1:8080").rstrip("/")

ST = {"na_fila": "na fila", "rodando": "rodando", "concluida": "concluído", "falhou": "falhou",
      "cancelada": "cancelado"}


def opt(a, name, default=None):
    if name in a:
        i = a.index(name)
        if i + 1 < len(a):
            v = a[i + 1]
            del a[i:i + 2]
            return v
    return default


def fail(r):
    if r.get("error"):
        print(r["error"])
        return True
    return False


def provider_opts(a, model):
    """--provedor P [--url URL] [--chave CHAVE] -> dict do provedor próprio (pergunta a chave sem eco se faltar)."""
    pid = opt(a, "--provedor")
    url, key = opt(a, "--url"), opt(a, "--chave")
    if not pid:
        return None
    try:
        p = conexoes.preset(pid)
    except conexoes.ConfigError:
        print("provedores: " + ", ".join(x["id"] for x in conexoes.PRESETS))
        raise SystemExit(2)
    if key is None and not p.get("semchave") and sys.stdin.isatty():
        key = getpass.getpass("Chave da API (não aparece na tela; Enter = manter a salva): ")
    return {"preset": pid, "base_url": url or "", "api_key": key or "", "model": model or ""}


def router(method, path, body=None):
    req = urllib.request.Request(ROUTER + path, method=method, data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except ValueError:
            return {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"error": f"AI Router indisponível: {e}"}


def main():
    a = sys.argv[1:]
    cmd = a.pop(0) if a else "status"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "status":
        r = paje_op("status", 10)
        if fail(r):
            return 1
        print(f"Pajé: {r['rodando']} trabalho(s) rodando, {r['na_fila']} na fila (até {r['max_paralelo']} em "
              f"paralelo) | {r['agentes']} de {r['max_subagentes']} subagentes")
        return 0
    if cmd == "agentes":
        r = paje_op("agents/list", 10)
        if fail(r):
            return 1
        for x in r["agentes"]:
            print(f"{x['id']:<20} {x['nome']:<22} {x['estado']:<12} conexão={x['conexao']}"
                  f"{'/' + x['modelo'] if x['modelo'] else ''}  tokens hoje={x['tokens_hoje']}/"
                  f"{x['orcamento_tokens_dia']}  ({'criado pelo Pajé' if x['criado_por'] == 'paje' else 'seu'})")
            print(f"{'':<20} {x['papel']}  | ferramentas: {', '.join(x['ferramentas']) or 'nenhuma'}")
        if not r["agentes"]:
            print("Nenhum subagente. Crie: paje novo Pesquisador --base pesquisador")
        return 0
    if cmd == "agente" and a:
        r = paje_op("agents/get", 10, id=a[0])
        if fail(r):
            return 1
        print(open(r["agente"]["arquivo"], encoding="utf-8").read())
        return 0
    if cmd == "novo" and a:
        params = {"papel": opt(a, "--papel"), "modelo_base": opt(a, "--base"), "conexao": opt(a, "--conexao"),
                  "modelo": opt(a, "--modelo")}
        tools = opt(a, "--ferramentas")
        if tools is not None:
            params["ferramentas"] = [t.strip() for t in tools.split(",") if t.strip()]
        alma = opt(a, "--alma")
        if alma:
            params["alma"] = open(alma, encoding="utf-8").read()
        own = provider_opts(a, params.get("modelo"))
        params["nome"] = " ".join(a)
        agent_id = souls.slugify(params["nome"])
        if own:
            try:
                params["conexao"], params["modelo"] = conexoes.set_agent_provider(agent_id, own)
            except conexoes.ConfigError as e:
                print(e.msg)
                return 1
        r = paje_op("agents/create", 10, **{k: v for k, v in params.items() if v is not None})
        if fail(r):
            if own:
                conexoes.remove_agent_provider(agent_id)
            return 1
        print(f"Criado: {r['agente']['nome']} ({r['agente']['id']}) — alma em {r['agente']['arquivo']}"
              + (f"\nProvedor próprio: conexão {params['conexao']}, modelo {params['modelo'] or '(padrão)'}"
                 if own else ""))
        return 0
    if cmd == "provedor" and a:
        agent_id = a.pop(0)
        if "--remover" in a:
            conexoes.remove_agent_provider(agent_id)
            r = paje_op("agents/update", 10, id=agent_id, conexao=opt(a, "--conexao") or "principal", modelo="")
            print("provedor próprio removido; usando a conexão " + (r.get("agente") or {}).get("conexao", "?")
                  if not fail(r) else "")
            return 0 if not r.get("error") else 1
        own = provider_opts(a, opt(a, "--modelo"))
        if not own:
            print("uso: paje provedor ID --provedor custom|openai|anthropic|... [--url URL] [--chave CHAVE] "
                  "[--modelo M]  |  paje provedor ID --remover")
            return 2
        try:
            conn, model = conexoes.set_agent_provider(agent_id, own)
        except conexoes.ConfigError as e:
            print(e.msg)
            return 1
        r = paje_op("agents/update", 10, id=agent_id, conexao=conn, modelo=model)
        if fail(r):
            return 1
        print(f"{r['agente']['nome']}: provedor próprio salvo (conexão {conn}, modelo {model or '(padrão)'})")
        return 0
    if cmd == "modelos":
        own = provider_opts(a, None)
        if own:                                     # antes de salvar: lista com a URL/chave informadas
            try:
                provider, base, key = conexoes.preset_params(own)
            except conexoes.ConfigError as e:
                print(e.msg)
                return 1
            r = router("POST", "/tupi/models", {"provider": provider, "base_url": base, "api_key": key})
            models = r.get("models") or []
        elif a:                                     # de um subagente (ou conexão) já salvo
            g = paje_op("agents/get", 10, id=a[0])
            conn = (g.get("agente") or {}).get("conexao") or a[0]
            r = router("GET", "/v1/models?conexao=" + urllib.parse.quote(conn))
            models = [m.get("id") for m in r.get("data") or []]
        else:
            print("uso: paje modelos ID_DO_AGENTE  |  paje modelos --provedor P [--url URL] [--chave CHAVE]")
            return 2
        if r.get("error"):
            print(r["error"] if isinstance(r["error"], str) else r["error"].get("message", r["error"]))
            return 1
        print("\n".join(models) or "(nenhum modelo)")
        return 0
    if cmd == "apagar" and a:
        r = paje_op("agents/delete", 10, id=a[0])
        if r.get("ok"):
            conexoes.remove_agent_provider(a[0])     # o provedor próprio vai junto
        print("apagado" if r.get("ok") else r.get("error"))
        return 0 if r.get("ok") else 1
    if cmd == "trabalho" and len(a) >= 2:
        titulo = opt(a, "--titulo")
        agente, instr = a[0], " ".join(a[1:])
        r = paje_op("tasks/create", 10, agente=agente, titulo=titulo or instr[:60], instrucoes=instr, origem="usuario")
        if fail(r):
            return 1
        print(f"Trabalho {r['trabalho']['id']} para {r['trabalho']['agente_nome']}: na fila")
        return 0
    if cmd == "trabalhos":
        r = paje_op("tasks/list", 10, limite=40)
        if fail(r):
            return 1
        for t in r["trabalhos"]:
            st = "aguardando sua aprovação" if t.get("aguardando_aprovacao") else ST.get(t["status"], t["status"])
            print(f"{t['id']}  {st:<24} {t['agente_nome']:<20} {t['titulo'][:60]}  ({t['origem']}, {t['tokens']} tokens)")
        return 0
    if cmd == "ver" and a:
        r = paje_op("tasks/get", 10, id=a[0])
        if fail(r):
            return 1
        t = r["trabalho"]
        print(f"[{t['id']}] {t['titulo']} — {t['agente_nome']} — {ST.get(t['status'], t['status'])} — "
              f"conexão {t.get('conexao') or 'principal'}"
              f"{', modelo ' + t['modelo_usado'] if t.get('modelo_usado') else ''} — {t.get('tokens', 0)} tokens")
        for s in t.get("passos") or []:
            print(f"  · {s.get('ferramenta')}: {s.get('status')} — {s.get('resumo') or ''} "
                  f"{'(' + s['motivo'] + ')' if s.get('motivo') else ''}")
        print("\n" + (t.get("resultado") or t.get("erro") or "(sem relatório ainda)"))
        return 0
    if cmd == "esperar" and a:
        secs = int(opt(a, "--segundos", "300"))
        r = paje_op("tasks/wait", secs + 30, ids=a, segundos=secs)
        if fail(r):
            return 1
        for t in r["trabalhos"]:
            print(f"[{t['id']}] {t['agente_nome']}: {ST.get(t['status'], t['status'])}\n"
                  f"{t.get('resultado') or t.get('erro') or ''}\n")
        return 0 if r.get("terminados") else 2
    if cmd == "cancelar" and a:
        r = paje_op("tasks/cancel", 10, id=a[0])
        print("cancelado" if r.get("ok") else r.get("error"))
        return 0 if r.get("ok") else 1
    if cmd == "harness":
        r = paje_op("harness", 15)
        if fail(r):
            return 1
        print("== Constituição do Pajé (fixa) ==\n" + r["constituicao_paje"] + "\n")
        print("== Alma do Pajé (editável: " + r["arquivos"]["alma_paje"] + ") ==\n" + r["alma_paje"]["alma"] + "\n")
        print("== Ferramentas de orquestração ==")
        for f in r["ferramentas_orquestracao"]:
            print(f"  {f['nome']}: {f['descricao']}")
        print("== Ferramentas que subagentes podem receber ==\n  " + ", ".join(r["ferramentas_de_subagente"]))
        print("== Conexões de IA ==")
        for c in r["conexoes"]:
            print(f"  {c['nome']}: {c.get('provider')} / {c.get('model')} ({'ok' if c.get('configured') else 'sem chave'})")
        print("== Limites ==\n  " + json.dumps(r["limites"], ensure_ascii=False))
        print("== Arquivos ==\n  " + json.dumps(r["arquivos"], ensure_ascii=False))
        if r["arquivos"].get("documentacao"):
            print("Documentação completa do harness: " + r["arquivos"]["documentacao"])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
