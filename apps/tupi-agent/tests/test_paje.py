"""Pajé: almas (.soul.md), motor de trabalhos em paralelo, limites e regras (IA e executor falsos)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
TMP = tempfile.mkdtemp(prefix="tupi-paje-test-")
os.environ["XDG_DATA_HOME"] = TMP
os.environ["TUPI_PAJE_MAX_PARALLEL"] = "3"
os.environ["TUPI_PAJE_MAX_SUBAGENTS"] = "5"
from tupi_agent import paje as PJ  # noqa: E402
from tupi_agent import souls as S  # noqa: E402
from tupi_agent.tools import ORCH_TOOLS  # noqa: E402

S.DATA = os.path.join(TMP, "tupios-ia", "paje")
PJ.HALT_POLL = 0.2
PJ.MAX_PARALLEL, PJ.MAX_SUBAGENTS = 3, 5

CALLS = []          # pedidos que chegaram à "IA"
EXEC = []           # ferramentas que chegaram ao "executor"
HALTED = {"v": False}


def fake_router(payload):
    """IA roteirizada: 1º passo chama a ferramenta pedida na tarefa; depois responde o relatório."""
    CALLS.append(payload)
    msgs = payload["messages"]
    task = msgs[1]["content"]
    if msgs[-1]["role"] == "tool":
        return {"model": payload.get("model") or "padrao", "usage": {"total_tokens": 100},
                "choices": [{"message": {"role": "assistant", "content": "Relatório: " + msgs[-1]["content"][:80]}}]}
    tool = "run_command" if "dormir" in task else ("write_file" if "escrever" in task else
                                                    "list_dir" if "listar" in task else None)
    if tool is None:
        return {"usage": {"total_tokens": 50}, "choices": [{"message": {"role": "assistant", "content": "Pronto."}}]}
    args = {"command": "sleep 1"} if tool == "run_command" else {"path": "~"}
    return {"usage": {"total_tokens": 100}, "choices": [{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": tool, "arguments": json.dumps(args)}}]}}]}


def fake_exec(op, timeout=None, **kw):
    if op == "status":
        return {"halted": HALTED["v"]}
    if op == "pending":
        return {"pending": []}
    if op == "log":
        return {"ok": True}
    if op == "run":
        if HALTED["v"]:                          # executor parado: nega tudo (como o execd real)
            return {"status": "denied", "reason": "A IA está PARADA (PARAR IA).", "summary": kw["tool"]}
        EXEC.append(dict(kw, t0=time.time()))
        if kw["tool"] == "run_command":
            time.sleep(1.0)
        if HALTED["v"]:
            return {"status": "error", "exit_code": 0, "result": {"output": "interrompido"}, "summary": kw["tool"]}
        return {"status": "ok", "exit_code": 0, "result": {"output": f"{kw['tool']} feito"}, "summary": kw["tool"],
                "onde": "PD", "approved_by": "política"}
    return {}


PJ.router_chat = fake_router
PJ.exec_op = fake_exec
ENGINE = PJ.start()


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def api(method, **p):
    return PJ.handle({"method": method, "params": p})


def wait_done(ids, timeout=10):
    return api("tasks/wait", ids=ids, segundos=timeout)


class Souls(unittest.TestCase):
    def test_roundtrip_and_restrictions(self):
        meta, body = S.parse(S.serialize({"nome": "Teste", "papel": "x", "ferramentas": ["read_file", "st_promote",
                                                                                       "criar_subagente"]}, "# Alma\nOi"))
        a = S.normalize_agent(meta, body)
        self.assertEqual(a["ferramentas"], ["read_file"])            # nunca promover nem orquestrar
        self.assertEqual(a["alma"], "# Alma\nOi")
        self.assertEqual(a["conexao"], "principal")
        self.assertFalse(set(S.SUB_TOOLS) & ORCH_TOOLS)
        self.assertNotIn("st_promote", S.SUB_TOOLS)

    def test_comments_and_defaults_in_header(self):
        meta, _ = S.parse("---\nnome: Ana   # comentário\nconexao: claude-extra\nrotina_minutos: 30\n---\ncorpo\n")
        a = S.normalize_agent(meta, "corpo")
        self.assertEqual((a["nome"], a["conexao"], a["rotina_minutos"]), ("Ana", "claude-extra", 30))
        self.assertEqual(a["ferramentas"], S.DEFAULT_SUB_TOOLS)

    def test_invalid_names(self):
        for bad in ("Pajé", "paje", "", "x" * 41, "rm -rf /;"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    S.clean_name(bad)

    def test_prompts(self):
        a = S.normalize_agent({"nome": "Pesq", "papel": "pesquisa"}, "Sou curioso.")
        p = S.sub_system_prompt(a)
        self.assertTrue(p.startswith("Você é Pesq, um SUBAGENTE do Pajé"))
        self.assertIn("Sou curioso.", p)
        self.assertLess(p.index("Regras fixas"), p.index("Sou curioso."))   # constituição antes da alma
        pp = S.paje_system_prompt([a])
        self.assertIn("Você é o Pajé", pp)
        self.assertIn("Pesq (id pesq)", pp)
        self.assertIn("delegar_tarefas", pp)


class Engine(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for name, tools in (("Leitor", ["list_dir", "read_file"]), ("Dorminhoco", ["run_command"]),
                            ("Sem Escrita", ["read_file"])):
            r = api("agents/create", nome=name, papel="teste", alma=f"Alma de {name}", ferramentas=tools)
            assert r.get("ok"), r

    def setUp(self):
        CALLS.clear()
        EXEC.clear()
        HALTED["v"] = False

    def test_parallel_tasks_really_overlap(self):
        r = api("tasks/create_many", tarefas=[{"agente": "dorminhoco", "titulo": f"t{i}", "instrucoes": "dormir"}
                                              for i in range(3)], origem="paje", conversa="c-par")
        ids = [t["id"] for t in r["trabalhos"]]
        t0 = time.time()
        res = wait_done(ids)
        took = time.time() - t0
        self.assertTrue(res["terminados"], res)
        self.assertTrue(all(t["status"] == "concluida" for t in res["trabalhos"]), res)
        self.assertLess(took, 2.5, "3 trabalhos de 1 s em paralelo deveriam levar ~1 s, não 3 s")
        starts = sorted(e["t0"] for e in EXEC)
        self.assertLess(starts[-1] - starts[0], 0.9, "os três começaram juntos")
        self.assertEqual({e["agente"] for e in EXEC}, {"Dorminhoco"})          # o nome vai para a aprovação/auditoria
        self.assertTrue(all(e["session"].startswith("tarefa-") for e in EXEC))

    def test_wait_by_conversation_and_report_text(self):
        api("tasks/create_many", tarefas=[{"agente": "Leitor", "titulo": "ler", "instrucoes": "listar a pasta"}],
            conversa="c-wait")
        res = api("tasks/wait", conversa="c-wait", segundos=10)
        self.assertTrue(res["terminados"])
        self.assertIn("Relatório:", res["trabalhos"][0]["resultado"])
        self.assertIn("list_dir feito", res["trabalhos"][0]["resultado"])

    def test_soul_restricts_tools(self):
        r = api("tasks/create", agente="sem-escrita", titulo="x", instrucoes="escrever um arquivo")
        t = wait_done([r["trabalho"]["id"]])["trabalhos"][0]
        self.assertEqual(t["status"], "concluida")
        self.assertEqual(EXEC, [], "write_file não está na alma: o executor nem foi chamado")
        self.assertEqual(t["passos"][0]["status"], "negado")
        tools_sent = [f["function"]["name"] for f in CALLS[0].get("tools", [])]
        self.assertEqual(tools_sent, ["read_file"])

    def test_unknown_agent_and_limits(self):
        r = api("tasks/create_many", tarefas=[{"agente": "ninguem", "titulo": "x", "instrucoes": "y"}])
        self.assertFalse(r["ok"])
        self.assertIn("não encontrado", r["trabalhos"][0]["erro"])
        api("agents/create", nome="Extra Um", papel="p", alma="a")
        api("agents/create", nome="Extra Dois", papel="p", alma="a")
        with self.assertRaises(ValueError):
            api("agents/create", nome="Extra Tres", papel="p", alma="a")      # máximo 5 subagentes
        with self.assertRaises(ValueError):
            api("agents/create", nome="Leitor", papel="p", alma="a")          # nome repetido
        api("agents/delete", id="extra-um")
        api("agents/delete", id="extra-dois")

    def test_budget(self):
        api("agents/update", id="leitor", orcamento_tokens_dia=1000)
        ENGINE.add_tokens("leitor", 5000)
        r = api("tasks/create", agente="leitor", titulo="x", instrucoes="listar")
        t = wait_done([r["trabalho"]["id"]])["trabalhos"][0]
        self.assertEqual(t["status"], "falhou")
        self.assertIn("orçamento diário", t["erro"])
        api("agents/update", id="leitor", orcamento_tokens_dia=10_000_000)

    def test_cancel_and_parar_ia(self):
        r = api("tasks/create_many", tarefas=[{"agente": "dorminhoco", "titulo": f"c{i}", "instrucoes": "dormir"}
                                              for i in range(5)])
        ids = [t["id"] for t in r["trabalhos"]]
        time.sleep(0.3)
        HALTED["v"] = True                            # PARAR IA no executor
        res = wait_done(ids, 10)
        with self.assertRaises(ValueError):           # parado: nenhum trabalho novo começa
            api("tasks/create", agente="dorminhoco", titulo="depois", instrucoes="dormir")
        r2 = api("tasks/create_many", tarefas=[{"agente": "dorminhoco", "titulo": "d", "instrucoes": "dormir"}])
        self.assertFalse(r2["ok"])
        self.assertIn("PARAR IA", r2["trabalhos"][0]["erro"])
        HALTED["v"] = False
        self.assertTrue(res["terminados"])
        self.assertTrue(any(t["status"] == "cancelada" for t in res["trabalhos"]), res)
        self.assertTrue(all(t["status"] in ("cancelada", "concluida") for t in res["trabalhos"]))

    def test_model_and_connection_per_agent(self):
        api("agents/create", nome="Claudio", papel="p", alma="a", ferramentas=[], conexao="claude-extra",
            modelo="claude-opus-5-5")
        r = api("tasks/create", agente="Claudio", titulo="x", instrucoes="responda")
        wait_done([r["trabalho"]["id"]])
        sent = CALLS[-1]
        self.assertEqual(sent["tupi_conexao"], "claude-extra")
        self.assertEqual(sent["model"], "claude-opus-5-5")
        self.assertNotIn("tools", sent)
        api("agents/delete", id="claudio")

    def test_edit_whole_soul_file(self):
        txt = api("agents/get", id="leitor")["agente"]
        r = api("agents/update", id="leitor", texto="---\nnome: Leitor\npapel: novo papel\nferramentas: list_dir, "
                                                    "st_promote\n---\n# Nova alma\n")
        self.assertEqual(r["agente"]["papel"], "novo papel")
        self.assertEqual(r["agente"]["ferramentas"], ["list_dir"])
        self.assertIn("Nova alma", read(S.soul_path("leitor")))
        api("agents/update", id="leitor", ferramentas=txt["ferramentas"], papel="teste")

    def test_paje_soul_and_harness(self):
        r = api("paje/soul", texto="---\nnome: Pajé\nconexao: principal\n---\n# Quem eu sou\nSou o Pajé de teste.\n")
        self.assertIn("Pajé de teste", r["alma"]["alma"])
        h = api("harness")
        self.assertIn("Você é o Pajé", h["constituicao_paje"])
        self.assertIn("criar_subagente", [f["nome"] for f in h["ferramentas_orquestracao"]])
        self.assertEqual(h["limites"]["max_paralelo"], 3)
        self.assertTrue(h["arquivos"]["alma_paje"].endswith("paje.soul.md"))

    def test_paje_created_agent_is_marked(self):
        r = api("agents/create", nome="Ajudante", papel="p", alma="a", criado_por="paje")
        self.assertEqual(r["agente"]["criado_por"], "paje")
        self.assertIn("criado_por: paje", read(S.soul_path("ajudante")))
        api("agents/delete", id="ajudante")


class ComputerUse(unittest.TestCase):
    def test_screenshots_go_as_images_and_old_ones_are_dropped(self):
        shot = os.path.join(TMP, "tela.png")
        with open(shot, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nfake")
        msgs = [{"role": "system", "content": "s"}]
        for _ in range(3):
            PJ.add_screenshots(msgs, [shot])
        imgs = [m for m in msgs if isinstance(m["content"], list)]
        self.assertEqual(len(imgs), 2)
        self.assertTrue(imgs[-1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertIn("removido", msgs[1]["content"])

    def test_token_estimate_does_not_count_base64(self):
        big = "data:image/png;base64," + "A" * 400000
        p = {"messages": [{"role": "user", "content": [{"type": "text", "text": "oi"},
                                                       {"type": "image_url", "image_url": {"url": big}}]}]}
        self.assertLess(PJ.estimate_tokens(p), 2000)
        self.assertGreaterEqual(PJ.estimate_tokens(p), PJ.IMAGE_TOKENS)

    def test_computer_agents_get_more_steps_and_budget(self):
        r = api("agents/create", nome="Maos", papel="p", alma="a", ferramentas=["use_computer", "open_file"])
        self.assertEqual(r["agente"]["ferramentas"], ["use_computer", "open_file"])
        self.assertEqual(r["agente"]["orcamento_tokens_dia"], S.DEFAULT_BUDGET * 4)
        self.assertGreater(PJ.MAX_STEPS_COMPUTER, PJ.MAX_STEPS)
        api("agents/delete", id="maos")
        r = api("agents/create", modelo_base="operador")
        self.assertIn("use_computer", r["agente"]["ferramentas"])
        api("agents/delete", id=r["agente"]["id"])


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=1)
