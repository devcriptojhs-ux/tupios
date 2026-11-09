"""tupi-agent — controle da IA pelo terminal.

  tupi-agent status | stop | resume | pending | history
  tupi-agent devolver                      a IA para de usar o mouse e o teclado (pede aprovação de novo)
  tupi-agent approve ID [--session] [--confirm-destructive] | deny ID | undo AÇÃO
  tupi-agent run FERRAMENTA 'JSON'        executa uma ferramenta (passa pela política)
  tupi-agent check FERRAMENTA 'JSON'      só mostra o que a política decidiria
  tupi-agent policy [show]
  tupi-agent policy mode restrito|perguntar|livre [--prazo 1h|boot|sempre]
  tupi-agent policy set CATEGORIA allow|ask|deny|default
  tupi-agent policy reset
  tupi-agent st                            estado do ST (ambiente de ensaio) e da mudança em ensaio
  tupi-agent st diff                       o que muda entre o PD e o ensaio
  tupi-agent st discard                    descarta o ensaio (o PD não muda)
"""
import json
import os
import sys

from . import policy as P
from .tools import exec_op, perm_op

ICON = {"allow": "✅ liberado", "ask": "❓ pergunta", "deny": "⛔ negado"}


def show(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def show_policy(pol):
    if pol.get("error"):
        print(pol["error"])
        return 1
    print(f"Modo: {pol['mode'].upper()}  (definido por: {pol['mode_source']})")
    if pol.get("livre"):
        print(f"  ⚠️  MODO LIVRE ATIVO — prazo: {pol['livre'].get('scope')}")
    for n in pol.get("notes", []):
        print("  aviso:", n)
    print()
    for cat in pol["categories"]:
        crit = " (crítica)" if cat in pol["critical"] else ""
        print(f"  {ICON[pol['table'][cat]]:<14} {pol['labels'][cat]}{crit}  [{pol['sources'][cat]}]")
    return 0


def confirm(text, expected):
    if not sys.stdin.isatty():
        print("Esta mudança exige confirmação digitada num terminal.")
        return False
    return input(text).strip() == expected


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "status"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "status":
        r = exec_op("status", 10)
        if r.get("error"):
            print(r["error"])
            return 1
        print(f"IA: {'PARADA' if r['halted'] else 'ativa'} | modo: {r['policy'].get('mode')} | "
              f"aguardando aprovação: {len(r['pending'])} | rodando agora: {r['running']}")
        return 0
    if cmd == "stop":
        r = exec_op("stop", 60)
        print("🛑 IA PARADA: tudo cancelado." if r.get("ok") else r)
        if r.get("ok"):
            print(f"   pedidos cancelados: {r['cancelled']}. Para voltar: tupi-agent resume")
        return 0 if r.get("ok") else 1
    if cmd == "resume":
        r = exec_op("resume", 10)
        print("▶️  IA retomada." if r.get("ok") else r)
        return 0
    if cmd == "devolver":
        r = exec_op("revoke_control", 10)
        print("🖱  Controle do mouse e do teclado devolvido a você." if r.get("ok") else r)
        return 0 if r.get("ok") else 1
    if cmd == "pending":
        for p in exec_op("pending", 10).get("pending", []):
            flag = "  ⚠️ DESTRUTIVO" if p["destructive"] else ""
            who = f"{p['agente']} (subagente do Pajé)" if p.get("agente") else "Pajé"
            print(f"[{p['id']}] {p['summary']}  (risco {p['risk']}, no {p.get('onde') or 'PD'}){flag}\n"
                  f"    pedido por: {who}\n    {p['reason']}")
            if p.get("detalhes"):
                print("    " + "\n    ".join(str(p["detalhes"]).splitlines()[:60]))
        return 0
    if cmd == "st":
        sub = a[1] if len(a) > 1 else "status"
        if sub == "discard":
            r = exec_op("st_discard", 300)
            print(r.get("mensagem") or r.get("error") or r)
            return 0 if r.get("ok") else 1
        s = exec_op("st_status", 60)
        if s.get("error") and "disponivel" not in s:
            print(s["error"])
            return 1
        print(f"ST: {'disponível' if s['disponivel'] else 'INDISPONÍVEL'} | container: {s.get('container')}")
        if not s["disponivel"]:
            print("  " + s.get("motivo", ""))
        e = s.get("ensaio")
        if e:
            v = "OK" if e.get("verificacoes_ok") else "com problemas"
            print(f"Em ensaio [{e['id']}]: {e.get('descricao') or e.get('resumo')} (verificações: {v})")
            if sub == "diff":
                print(s.get("diff_texto") or "")
            elif s.get("verificacoes"):
                show(s["verificacoes"])
        elif s["disponivel"]:
            print("Nenhuma mudança em ensaio.")
        return 0
    if cmd in ("approve", "deny") and len(a) >= 2:
        decision = "deny" if cmd == "deny" else ("session" if "--session" in a else "once")
        r = exec_op("decide", 10, id=a[1], decision=decision, confirm_destructive="--confirm-destructive" in a)
        print("ok" if r.get("ok") else r.get("error"))
        return 0 if r.get("ok") else 1
    if cmd == "undo" and len(a) >= 2:
        r = exec_op("undo", 900, action_id=a[1])
        print("↩️  desfeito" if r.get("ok") else r.get("error"))
        return 0 if r.get("ok") else 1
    if cmd == "history":
        for x in exec_op("history", 10).get("actions", []):
            u = "  [pode desfazer]" if x.get("can_undo") else ""
            print(f"{x['action_id']}  {x['status']:<6} {x['summary']}{u}")
        return 0
    if cmd in ("run", "check") and len(a) >= 2:
        args = json.loads(a[2]) if len(a) > 2 else {}
        if cmd == "check":
            r = perm_op("tool_check", tool=a[1], args=args, home=os.path.expanduser("~"))
            print(f"{ICON.get(r.get('decision'), r.get('decision'))}: {r.get('summary')}")
            print(f"  categorias: {', '.join(r.get('categories', []))} | risco: {r.get('risk')}"
                  + (" | DESTRUTIVO" if r.get("destructive") else ""))
            print(f"  {r.get('reason')}")
            return 0
        r = exec_op("run", None, tool=a[1], args=args, session="cli",
                    display=os.environ.get("DISPLAY"), xauthority=os.environ.get("XAUTHORITY"))
        show(r)
        return 0 if r.get("status") == "ok" else 1
    if cmd == "policy":
        sub = a[1] if len(a) > 1 else "show"
        if sub == "show":
            return show_policy(perm_op("policy_show"))
        if sub == "reset":
            r = perm_op("policy_reset")
            print("Permissões restauradas para o padrão." if r.get("ok") else r.get("error"))
            return show_policy(r["policy"]) if r.get("ok") else 1
        if sub == "mode" and len(a) > 2:
            mode = a[2]
            req = {"mode": mode}
            if mode == "livre":
                scope = a[a.index("--prazo") + 1] if "--prazo" in a else "1h"
                print("⚠️  MODO LIVRE: a IA poderá fazer QUALQUER coisa no sistema sem perguntar, inclusive como "
                      "administrador (exceto alterar a própria política e apagar a auditoria).\n"
                      "   Comandos destrutivos continuam pedindo confirmação.")
                if not confirm("Digite LIVRE para continuar: ", "LIVRE"):
                    print("Cancelado.")
                    return 1
                if not confirm("Confirme de novo digitando ATIVAR MODO LIVRE: ", "ATIVAR MODO LIVRE"):
                    print("Cancelado.")
                    return 1
                req.update(confirm="ATIVAR MODO LIVRE", livre_scope=scope)
            r = perm_op("policy_set", **req)
            print("ok" if r.get("ok") else r.get("error"))
            return show_policy(r["policy"]) if r.get("ok") else 1
        if sub == "set" and len(a) > 3:
            cat, action = a[2], a[3]
            req = {"overrides": {cat: action}}
            if (cat in P.CRITICAL or cat in P.EXPLICIT_ONLY) and action in ("allow", "ask"):
                print(f"⚠️  \"{P.LABELS.get(cat, cat)}\" é uma categoria crítica.")
                if not confirm("Digite LIBERAR para confirmar: ", "LIBERAR"):
                    print("Cancelado.")
                    return 1
                req["confirm"] = "LIBERAR CATEGORIAS CRÍTICAS"
            r = perm_op("policy_set", **req)
            print("ok" if r.get("ok") else r.get("error"))
            return show_policy(r["policy"]) if r.get("ok") else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
