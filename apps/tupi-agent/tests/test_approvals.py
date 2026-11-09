"""Fluxo de aprovação do executor: pending -> uma vez / sessão / negar / timeout, destrutivo, PARAR, limite."""
import os
import sys
import threading
import time
import unittest

os.environ["TUPI_AGENT_APPROVAL_TIMEOUT"] = "2"
os.environ["TUPI_AGENT_ACTIONS_PER_MINUTE"] = "50"
os.environ["TUPI_AGENT_STATE"] = "/tmp/tupi-agent-test-state"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tupi_agent import execd as E  # noqa: E402

# o módulo pode já ter sido importado por outro teste (antes das variáveis acima): fixa os valores
E.APPROVAL_TIMEOUT, E.RATE, E.STATE_DIR = 2, 50, "/tmp/tupi-agent-test-state"
UID = os.getuid()
EVENTS = []


def fake_perm(req):
    if req.get("type") == "policy_show":
        return {"mode": "restrito"}
    tool, args = req["tool"], req.get("args") or {}
    if tool == "list_dir":
        return {"decision": "allow", "request_id": "r-allow", "categories": ["read"], "summary": "listar"}
    if tool == "run_command" and "rm -rf" in args.get("command", ""):
        return {"decision": "ask", "request_id": "r-destr-" + str(time.time()), "categories": ["write_user_data"],
                "summary": "apagar", "destructive": True, "reason": "destrutivo"}
    if tool == "run_command":
        return {"decision": "ask", "request_id": "r-ask-" + str(time.time()), "categories": ["execute_binary"],
                "summary": args.get("command"), "reason": "precisa de aprovação"}
    return {"decision": "deny", "request_id": "r-deny", "categories": ["delete_system_file"], "reason": "negado"}


E.perm = fake_perm
E.event = lambda **kw: EVENTS.append(kw)
E.allowed_user = lambda uid: True
E.RUNNERS = {k: (lambda ctx, args: (0, {"ok": True})) for k in E.TOOLS}


def run_async(tool, args, session="s1"):
    box = {}
    t = threading.Thread(target=lambda: box.update(r=E.op_run(UID, {"tool": tool, "args": args, "session": session})))
    t.start()
    return t, box


def wait_pending(timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        p = E.op_status(UID)["pending"]
        if p:
            return p[0]
        time.sleep(0.05)
    raise AssertionError("nenhum pedido pendente apareceu")


class Approvals(unittest.TestCase):
    def setUp(self):
        E.S.__init__()
        EVENTS.clear()

    def test_allow_runs_without_asking(self):
        r = E.op_run(UID, {"tool": "list_dir", "args": {"path": "~"}})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["approved_by"], "política")

    def test_deny(self):
        r = E.op_run(UID, {"tool": "write_file", "args": {"path": "/etc/fstab"}})
        self.assertEqual(r["status"], "denied")

    def test_approve_once(self):
        t, box = run_async("run_command", {"command": "make"})
        p = wait_pending()
        self.assertEqual(p["summary"], "make")
        self.assertTrue(E.op_decide(UID, {"id": p["id"], "decision": "once"})["ok"])
        t.join(5)
        self.assertEqual(box["r"]["status"], "ok")
        self.assertEqual(box["r"]["approved_by"], "usuário")
        self.assertIn({"event": "aprovacao", "request_id": p["id"], "user": E.pwd.getpwuid(UID).pw_name,
                       "outcome": "once", "by": "usuário"}, EVENTS)

    def test_deny_by_user(self):
        t, box = run_async("run_command", {"command": "make"})
        p = wait_pending()
        E.op_decide(UID, {"id": p["id"], "decision": "deny"})
        t.join(5)
        self.assertEqual(box["r"]["status"], "denied")
        self.assertIn("negou", box["r"]["reason"])

    def test_timeout_becomes_deny(self):
        t0 = time.time()
        r = E.op_run(UID, {"tool": "run_command", "args": {"command": "make"}, "session": "s1"})
        self.assertGreaterEqual(time.time() - t0, 1.9)
        self.assertEqual(r["status"], "denied")
        self.assertEqual(r["approval"], "timeout")
        self.assertFalse(E.op_status(UID)["pending"])

    def test_session_grant(self):
        t, box = run_async("run_command", {"command": "make"})
        E.op_decide(UID, {"id": wait_pending()["id"], "decision": "session"})
        t.join(5)
        r = E.op_run(UID, {"tool": "run_command", "args": {"command": "make test"}, "session": "s1"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["approved_by"], "sessão")
        # outra sessão volta a perguntar
        t, box = run_async("run_command", {"command": "make"}, session="s2")
        E.op_decide(UID, {"id": wait_pending()["id"], "decision": "deny"})
        t.join(5)

    def test_destructive_needs_extra_confirmation_and_no_session_grant(self):
        t, box = run_async("run_command", {"command": "rm -rf ~/build"})
        p = wait_pending()
        self.assertTrue(p["destructive"])
        self.assertFalse(E.op_decide(UID, {"id": p["id"], "decision": "once"})["ok"])
        r = E.op_decide(UID, {"id": p["id"], "decision": "session", "confirm_destructive": True})
        self.assertEqual(r["decision"], "once")
        t.join(5)
        self.assertEqual(box["r"]["status"], "ok")
        # não virou "sempre": pergunta de novo
        t, box = run_async("run_command", {"command": "rm -rf ~/build"})
        wait_pending()
        E.op_stop(UID)
        t.join(5)

    def test_stop_cancels_pending_and_blocks_until_resume(self):
        t, box = run_async("run_command", {"command": "make"})
        wait_pending()
        self.assertTrue(E.op_stop(UID)["halted"])
        t.join(5)
        self.assertEqual(box["r"]["approval"], "cancelled")
        r = E.op_run(UID, {"tool": "list_dir", "args": {"path": "~"}})
        self.assertEqual(r["status"], "denied")
        self.assertIn("PARADA", r["reason"])
        E.op_resume(UID)
        self.assertEqual(E.op_run(UID, {"tool": "list_dir", "args": {"path": "~"}})["status"], "ok")
        self.assertTrue(any(e.get("event") == "parar_ia" for e in EVENTS))

    def test_stop_during_execution_marks_interrupted(self):
        started = threading.Event()

        def slow(ctx, args):           # simula um comando longo morto pelo PARAR IA (sai com 0)
            started.set()
            while not E.S.halted:
                time.sleep(0.02)
            return 0, {"output": ""}
        old = E.RUNNERS["list_dir"]
        E.RUNNERS["list_dir"] = slow
        try:
            box = {}
            t = threading.Thread(target=lambda: box.update(r=E.op_run(UID, {"tool": "list_dir", "args": {}})))
            t.start()
            self.assertTrue(started.wait(3))
            E.op_stop(UID)
            t.join(5)
            self.assertEqual(box["r"]["status"], "error")
            self.assertIn("PARAR IA", box["r"]["reason"])
            self.assertTrue(box["r"]["result"]["interrompida"])
        finally:
            E.RUNNERS["list_dir"] = old
            E.op_resume(UID)

    def test_rate_limit(self):
        old = E.RATE
        E.RATE = 3
        try:
            results = [E.op_run(UID, {"tool": "list_dir", "args": {}})["status"] for _ in range(4)]
            self.assertEqual(results, ["ok", "ok", "ok", "denied"])
        finally:
            E.RATE = old


POINTER = {"pos": [100, 100]}      # onde o ponteiro "está" (o teste mexe aqui para simular o usuário)
HELPER_CALLS = []


def fake_helper(subcmd, *args, stdin=None, **kw):
    import json as _j
    req = _j.loads(stdin or "{}")
    HELPER_CALLS.append(req)
    exp = req.get("esperado")
    if exp and list(exp) != POINTER["pos"]:
        return 5, {"assumiu": True, "error": "o usuário mexeu no mouse"}, False
    if req.get("action") in ("click", "move") and req.get("x") is not None:
        POINTER["pos"] = [req["x"], req["y"]]
    return 0, {"ok": True, "pos_real": list(POINTER["pos"]), "cursor": list(POINTER["pos"])}, False


def computer_perm(req):
    if req.get("type") == "policy_show":
        return {"mode": "restrito"}
    tool, args = req["tool"], req.get("args") or {}
    rid = f"r-{tool}-{time.time()}"
    if tool == "use_computer" and args.get("action") == "screenshot":
        return {"decision": "allow", "request_id": rid, "categories": ["read"], "summary": "ver a tela"}
    if tool in ("use_computer", "open_file"):
        return {"decision": "ask", "request_id": rid, "categories": ["control_desktop"], "summary": "clicar",
                "reason": "mouse"}
    if tool == "st_promote":
        return {"decision": "ask", "request_id": rid, "categories": ["promote_change"], "summary": "promover"}
    return fake_perm(req)


class ComputerControl(unittest.TestCase):
    """Usar o computador: "nesta conversa" até o usuário mexer no mouse; nunca com aprovação esperando."""

    def setUp(self):
        E.S.__init__()
        EVENTS.clear()
        HELPER_CALLS.clear()
        POINTER["pos"] = [100, 100]
        self.saved = (E.perm, E.helper, E.RUNNERS["use_computer"], E.CONTROL_IDLE)
        E.perm, E.helper = computer_perm, fake_helper
        E.RUNNERS["use_computer"] = E.tool_use_computer

    def tearDown(self):
        E.perm, E.helper, E.RUNNERS["use_computer"], E.CONTROL_IDLE = self.saved
        E.op_resume(UID)

    def click(self, x, y, session="s1"):
        return E.op_run(UID, {"tool": "use_computer", "args": {"action": "click", "x": x, "y": y},
                              "session": session, "display": ":0"})

    def approve_session(self, x=10, y=10):
        box = {}
        t = threading.Thread(target=lambda: box.update(r=self.click(x, y)))
        t.start()
        p = wait_pending()
        self.assertIn("Devolver o controle", p["detalhes"])
        E.op_decide(UID, {"id": p["id"], "decision": "session"})
        t.join(5)
        return box["r"]

    def test_session_grant_until_user_moves_the_mouse(self):
        r = self.approve_session(10, 10)
        self.assertEqual((r["status"], r["approved_by"]), ("ok", "usuário"))
        self.assertNotIn("esperado", HELPER_CALLS[-1])           # acabou de aprovar: não confere o ponteiro
        r = self.click(20, 20)                                     # mesma conversa: sem perguntar
        self.assertEqual((r["status"], r["approved_by"]), ("ok", "sessão"))
        self.assertEqual(HELPER_CALLS[-1]["esperado"], [10, 10])
        st = E.op_status(UID)["controle"]
        self.assertTrue(st["ativo"] and st["liberado"])
        POINTER["pos"] = [500, 400]                                # o usuário mexeu no mouse
        r = self.click(30, 30)
        self.assertEqual(r["status"], "error")
        self.assertIn("controle voltou para você", r["result"]["error"])
        self.assertTrue(any(e.get("event") == "controle_devolvido" for e in EVENTS))
        self.assertIsNone(E.op_status(UID)["controle"])
        box = {}
        t = threading.Thread(target=lambda: box.update(r=self.click(40, 40)))
        t.start()
        p = wait_pending()                                         # pede aprovação de novo
        E.op_decide(UID, {"id": p["id"], "decision": "deny"})
        t.join(5)

    def test_screenshot_needs_no_approval(self):
        r = E.op_run(UID, {"tool": "use_computer", "args": {"action": "screenshot"}, "session": "s1", "display": ":0"})
        self.assertEqual((r["status"], r["approved_by"]), ("ok", "política"))

    def test_never_acts_while_an_approval_is_waiting(self):
        self.approve_session()
        t, box = run_async("run_command", {"command": "make"}, session="outra")
        p = wait_pending()
        r = self.click(50, 50)
        self.assertEqual(r["status"], "error")
        self.assertIn("pedido de aprovação esperando", r["result"]["error"])
        E.op_decide(UID, {"id": p["id"], "decision": "deny"})
        t.join(5)
        self.assertEqual(self.click(50, 50)["status"], "ok")

    def test_one_agent_at_a_time(self):
        self.approve_session()
        with E.S.lock:
            E.S.control[UID] = {"session": "tarefa-x", "agente": "Pesquisador", "pos": None, "ultima": time.time()}
        r = self.click(5, 5)
        self.assertEqual(r["status"], "error")
        self.assertIn("Pesquisador está usando o mouse", r["result"]["error"])

    def test_grant_expires_when_idle_and_stop_revokes(self):
        self.approve_session()
        E.CONTROL_IDLE = 1
        time.sleep(1.2)
        box = {}
        t = threading.Thread(target=lambda: box.update(r=self.click(5, 5)))
        t.start()
        p = wait_pending()                                         # parado demais: pergunta de novo
        E.op_decide(UID, {"id": p["id"], "decision": "session"})
        t.join(5)
        E.CONTROL_IDLE = 600
        E.op_stop(UID)                                             # PARAR IA devolve o controle
        self.assertFalse(any("control_desktop" in c for c in E.S.grants.values()))
        self.assertIsNone(E.op_status(UID)["controle"])

    def test_paje_can_switch_conversations_but_two_tasks_wait(self):
        now = time.time()
        with E.S.lock:
            E.S.control[UID] = {"session": "s1", "agente": None, "pos": [10, 10], "ultima": now}
        self.assertIsNone(E.control_guard({"uid": UID, "session": "s2", "agente": None}))   # você trocou de conversa
        with E.S.lock:
            E.S.control[UID] = {"session": "tarefa-a", "agente": "Operador", "pos": None, "ultima": now}
        self.assertIn("Operador está usando", E.control_guard({"uid": UID, "session": "tarefa-b", "agente": "Operador"}))
        self.assertIn("Operador está usando", E.control_guard({"uid": UID, "session": "s1", "agente": None}))
        self.assertIsNone(E.control_guard({"uid": UID, "session": "tarefa-a", "agente": "Operador"}))

    def test_desktop_env_finds_apps_on_nixos(self):
        env = E.desktop_env({"uid": 1000, "home": "/home/tupi", "user": "tupi", "display": ":0",
                             "xauthority": "/home/tupi/.Xauthority", "desktop": "XFCE"})
        self.assertIn("/run/current-system/sw/share", env["XDG_DATA_DIRS"])
        self.assertIn("/etc/profiles/per-user/tupi/share", env["XDG_DATA_DIRS"])
        self.assertEqual((env["DISPLAY"], env["XDG_CURRENT_DESKTOP"]), (":0", "XFCE"))
        self.assertEqual(E.desktop_env({"uid": 1000, "home": "/h"}), {})              # sem tela: nada

    def test_open_file_refuses_programs_before_asking(self):
        t0 = time.time()
        r = E.op_run(UID, {"tool": "open_file", "args": {"path": "/bin/sh"}, "session": "s1", "display": ":0"})
        self.assertEqual(r["status"], "error")
        self.assertIn("não abre programas", r["reason"])
        self.assertLess(time.time() - t0, 1)                      # não ficou esperando aprovação
        r = E.op_run(UID, {"tool": "open_file", "args": {"path": "/nao/existe.pdf"}, "session": "s1", "display": ":0"})
        self.assertIn("não existe", r["reason"])

    def test_revoke_button(self):
        self.approve_session()
        self.assertTrue(E.op_revoke_control(UID)["tinha_controle"])
        self.assertIsNone(E.op_status(UID)["controle"])

    def test_promote_is_never_granted_for_the_session(self):
        saved = (E.ST.available, E.ST.load_stage, E.RUNNERS["st_promote"])
        E.ST.available = lambda: (True, "")
        E.ST.load_stage = lambda: {"mudanca_total": {"pacotes": ["hello"]}, "descricao": "x"}
        E.RUNNERS["st_promote"] = lambda ctx, args: (0, {"ok": True})
        try:
            for _ in range(2):
                box = {}
                t = threading.Thread(target=lambda: box.update(r=E.op_run(UID, {"tool": "st_promote", "args": {},
                                                                                 "session": "s1"})))
                t.start()
                p = wait_pending()                                 # pergunta TODA vez, mesmo depois de "sessão"
                E.op_decide(UID, {"id": p["id"], "decision": "session"})
                t.join(5)
                self.assertEqual(box["r"]["approved_by"], "usuário")
        finally:
            E.ST.available, E.ST.load_stage, E.RUNNERS["st_promote"] = saved


if __name__ == "__main__":
    unittest.main(verbosity=1)
