"""Fluxo do ST com executor falso: ensaio -> diff -> promover -> desfazer (e os casos de recusa).

As operações root (nix build, ligar o container, trocar a geração do PD) são simuladas por FakeRoot;
arquivos de configuração, estado do ensaio, verificações, diff e política são o código real."""
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tupi_agent import execd as E  # noqa: E402
from tupi_agent import policy as P  # noqa: E402
from tupi_agent import st as ST  # noqa: E402

UID = os.getuid()


def mkdirs(*parts):
    p = os.path.join(*parts)
    os.makedirs(p, exist_ok=True)
    return p


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def fake_system(root, name, bins, units, sysctl="", params="loglevel=4"):
    top = mkdirs(root, name)
    for b in bins:
        write(os.path.join(top, "sw", "bin", b), "#!/bin/sh\n")
    for u, content in units.items():
        write(os.path.join(root, "units", name, u), content)
        mkdirs(top, "etc", "systemd", "system")
        os.symlink(os.path.join(root, "units", name, u), os.path.join(top, "etc", "systemd", "system", u))
    write(os.path.join(top, "etc", "sysctl.d", "60-nixos.conf"), sysctl)
    write(os.path.join(top, "kernel-params"), params)
    return top


class FakeRoot:
    """Simula as operações privilegiadas (o que o executor faria como root)."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.calls = []
        self.present = {"bash"}
        self.fail_build = False
        self.new_failures = []
        self.current = fake_system(tmp, "sys-atual", ["ls"], {"sshd.service": "a"})
        self.base_st = fake_system(tmp, "st-base", ["ls"], {"sshd.service": "a"})
        self.st_top = fake_system(tmp, "st-ensaio", ["ls", "htop"], {"sshd.service": "a"})
        self.pd_top = fake_system(tmp, "pd-ensaio", ["ls", "htop"], {"sshd.service": "b", "novo.service": "c"},
                                  sysctl="vm.swappiness=10\n")
        self.profile = os.path.join(tmp, "profile-system")
        os.symlink(self.current, self.profile)

    def run(self, script, timeout):
        self.calls.append(script)
        if "nix-store -qR" in script:
            names = re.findall(r"echo 'PKG (\S+) novo'", script)
            return 0, "\n".join(f"PKG {n} {'presente' if n in self.present else 'novo'}" for n in names)
        if " build " in script:
            if self.fail_build and "st-base" not in script:
                return 1, "error: undefined variable 'pacote-quebrado'"
            if "st-base" in script:
                return 0, f"TOPLEVEL={self.base_st}"
            if "--profile" in script:
                return 0, f"TOPLEVEL={self.st_top}"
            return 0, f"TOPLEVEL={self.pd_top}"
        if "systemctl restart" in script:
            booted = self.st_top if self.st_top in script else self.base_st
            failed = ["foo.service"] + (self.new_failures if booted == self.st_top else [])
            return 0, "ST_STATE=degraded\n===FALHAS\n" + "\n".join(failed) + "\n===FIM"
        if "switch-to-configuration" in script:
            m = re.search(r"--set '([^']+)'", script)
            os.remove(self.profile)
            os.symlink(m.group(1), self.profile)
            return 0, "SNAPSHOT=\nativando a configuração..."
        if "systemctl is-active" in script:
            return 0, "active\n"
        if "command -v" in script:
            return 0, "/run/current-system/sw/bin/htop\n"
        if "systemctl stop" in script:
            return 0, ""
        return 0, ""

    def sys(self, cmd, timeout=30):
        if cmd[:2] == ["systemctl", "is-active"]:
            return 3, "inactive\n"
        if "diff-closures" in cmd:
            return 0, "htop: ∅ → 3.3.0, +1.2 MiB"
        return 0, ""


class STBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tupi-st-test-")
        self.flake = mkdirs(self.tmp, "etc-nixos")
        write(os.path.join(self.flake, "flake.nix"), "{ outputs = _: {}; }\n")
        write(os.path.join(self.flake, "nixos", "pd", "configuration.nix"), "{ ... }: { }\n")
        self.root = FakeRoot(self.tmp)
        self.saved = {k: getattr(ST, k) for k in ("FLAKE_DIR", "STATE", "ST_ENABLED", "LIVE", "CURRENT_SYSTEM",
                                                    "SYSTEM_PROFILE", "RUN", "SYS", "ST_PROFILE")}
        ST.FLAKE_DIR, ST.STATE = self.flake, mkdirs(self.tmp, "state", "st")
        ST.ST_ENABLED, ST.LIVE = True, False
        ST.CURRENT_SYSTEM, ST.SYSTEM_PROFILE = self.root.current, self.root.profile
        ST.ST_PROFILE = os.path.join(self.tmp, "st-profile")
        ST.RUN, ST.SYS = self.root.run, self.root.sys

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(ST, k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pd_cfg(self):
        return json.load(open(ST.cfg_json(self.flake))) if os.path.exists(ST.cfg_json(self.flake)) else None


class StageDiffPromoteUndo(STBase):
    def test_full_flow(self):
        code, res = ST.stage({"pacotes": ["htop"]}, "instalar o htop")
        self.assertEqual(code, 0, res)
        self.assertTrue(res["verificacoes"]["ok"])
        self.assertEqual(res["verificacoes"]["unidades_com_falha_novas"], [])
        self.assertEqual(res["verificacoes"]["unidades_com_falha_ja_existentes"], ["foo.service"])
        self.assertEqual(res["verificacoes"]["programas_novos"], [{"programa": "htop", "encontrado_no_st": True}])
        self.assertEqual(res["onde"], "ST")
        self.assertIsNone(self.pd_cfg(), "o ensaio não pode mexer na configuração do PD")
        self.assertEqual(os.path.realpath(self.root.profile), self.root.current, "o ensaio não troca o PD")
        st = ST.load_stage()
        self.assertEqual(st["mudanca_total"], {"pacotes": ["htop"]})
        # diff legível
        code, d = ST.current_diff()
        self.assertEqual(code, 0)
        self.assertIn('"htop"', d["diff"]["configuracao"])
        self.assertIn("htop: ∅ → 3.3.0", d["texto"])
        self.assertEqual(d["diff"]["servicos"]["novos"], ["novo.service"])
        self.assertEqual(d["diff"]["servicos"]["alterados"], ["sshd.service"])
        self.assertIn("+vm.swappiness=10", d["diff"]["sysctl"])
        self.assertEqual([f["arquivo"] for f in d["diff"]["arquivos"]], ["nixos/pd/agent-config.json"])
        # promover
        action_dir = mkdirs(self.tmp, "undo", "a1")
        code, res, undo = ST.promote(action_dir)
        self.assertEqual(code, 0, res)
        self.assertEqual(os.path.realpath(self.root.profile), self.root.pd_top)
        self.assertEqual(self.pd_cfg()["pacotes"], ["htop"])
        self.assertEqual(undo["old_system"], self.root.current)
        self.assertIsNone(ST.load_stage())
        self.assertFalse(os.path.exists(ST.work()))
        # desfazer: volta a geração e a configuração
        ok, r = ST.undo_promote(undo)
        self.assertTrue(ok, r)
        self.assertEqual(os.path.realpath(self.root.profile), self.root.current)
        self.assertIsNone(self.pd_cfg(), "o arquivo criado pela promoção some no Desfazer")

    def test_changes_accumulate_and_nix_snippet_is_rendered(self):
        ST.stage({"pacotes": ["htop"]}, "htop")
        code, res = ST.stage({"nix": "services.openssh.enable = true;", "servico_alvo": "sshd"}, "ligar o ssh")
        self.assertEqual(code, 0, res)
        self.assertEqual(res["verificacoes"]["servico_alvo"], {"unidade": "sshd.service", "estado": "active"})
        st = ST.load_stage()
        self.assertEqual(len(st["mudancas"]), 2)
        self.assertEqual(st["mudanca_total"]["pacotes"], ["htop"])
        with open(ST.cfg_nix(ST.work())) as f:
            extra = f.read()
        self.assertIn("services.openssh.enable = true;", extra)
        self.assertIn("({ config, lib, pkgs, ... }: {", extra)

    def test_package_already_in_pd_is_nothing_to_do(self):
        self.root.present.add("htop")
        code, res = ST.stage({"pacotes": ["htop"]}, "htop")
        self.assertEqual(code, 0)
        self.assertTrue(res["nada_a_fazer"])
        self.assertIn("já estão instalados", res["mensagem"])
        self.assertIsNone(ST.load_stage())

    def test_build_failure_changes_nothing(self):
        self.root.fail_build = True
        code, res = ST.stage({"pacotes": ["pacote-quebrado"]}, "x")
        self.assertEqual(code, 1)
        self.assertIn("NÃO construiu", res["error"])
        self.assertIsNone(ST.load_stage())
        self.assertIsNone(self.pd_cfg())

    def test_new_failed_unit_makes_checks_fail(self):
        self.root.new_failures = ["quebrado.service"]
        code, res = ST.stage({"pacotes": ["htop"]}, "htop")
        self.assertEqual(code, 1)
        self.assertFalse(res["verificacoes"]["ok"])
        self.assertEqual(res["verificacoes"]["unidades_com_falha_novas"], ["quebrado.service"])

    def test_sysctl_is_not_claimed_as_tested(self):
        code, res = ST.stage({"sysctl": {"vm.swappiness": 10}}, "swappiness")
        self.assertIn("NÃO ensaiado", res["verificacoes"]["kernel"])
        self.assertIn("mesmo kernel", res["verificacoes"]["kernel"].lower())

    def test_promote_refused_if_pd_config_changed_after_staging(self):
        ST.stage({"pacotes": ["htop"]}, "htop")
        write(os.path.join(self.flake, "nixos", "pd", "configuration.nix"), "{ ... }: { mudou = 1; }\n")
        code, res, undo = ST.promote(mkdirs(self.tmp, "undo", "a2"))
        self.assertEqual(code, 2)
        self.assertIn("mudou depois do ensaio", res["error"])
        self.assertIsNone(undo)
        self.assertEqual(os.path.realpath(self.root.profile), self.root.current)

    def test_discard(self):
        ST.stage({"pacotes": ["htop"]}, "htop")
        code, res = ST.discard()
        self.assertEqual(code, 0)
        self.assertIsNone(ST.load_stage())
        self.assertFalse(os.path.exists(ST.work()))
        self.assertIsNone(self.pd_cfg())

    def test_live_iso_is_unavailable_with_clear_message(self):
        ST.LIVE = True
        for code, res in (ST.stage({"pacotes": ["htop"]}, "x"), ST.current_diff(), ST.discard(),
                          ST.promote(self.tmp)[:2]):
            self.assertEqual(code, 3)
            self.assertIn("indisponível no modo live", res["error"])
            self.assertIn("instale o TUPIOS", res["error"].replace("Instale", "instale"))
        self.assertFalse(ST.status()["disponivel"])


class ExecutorIntegration(STBase):
    """Passa pelo op_run real: política real (modo livre), pedido de aprovação com o diff, Desfazer."""

    def setUp(self):
        super().setUp()
        self.events = []
        self.saved_e = (E.perm, E.event, E.allowed_user, dict(E.RUNNERS), E.STATE_DIR)
        E.STATE_DIR = mkdirs(self.tmp, "state")
        self.mode = "livre"

        def fake_perm(req):
            if req.get("type") == "policy_show":
                return {"mode": self.mode}
            c = P.classify(req["tool"], req.get("args") or {}, "/home/teste")
            d, reason = P.decide(c, P.effective_policy({"mode": self.mode}, {}))
            return dict(c.to_dict(), decision=d, reason=reason, request_id="r" + str(time.time()),
                        labels=[P.LABELS[x] for x in c.categories])
        E.perm = fake_perm
        E.event = lambda **kw: self.events.append(kw)
        E.allowed_user = lambda uid: True
        E.RUNNERS.update({"st_stage_change": E.tool_st_stage_change, "st_diff": E.tool_st_diff,
                          "st_promote": E.tool_st_promote, "st_discard": E.tool_st_discard})
        E.S.__init__()

    def tearDown(self):
        E.perm, E.event, E.allowed_user, runners, E.STATE_DIR = self.saved_e
        E.RUNNERS.clear()
        E.RUNNERS.update(runners)
        super().tearDown()

    def run_async(self, tool, args):
        box = {}
        t = threading.Thread(target=lambda: box.update(r=E.op_run(UID, {"tool": tool, "args": args,
                                                                         "session": "s1"})))
        t.start()
        return t, box

    def wait_pending(self):
        end = time.time() + 5
        while time.time() < end:
            p = E.op_status(UID)["pending"]
            if p:
                return p[0]
            time.sleep(0.05)
        raise AssertionError("nenhum pedido pendente")

    def test_stage_runs_in_livre_promote_always_asks_with_diff_then_undo(self):
        r = E.op_run(UID, {"tool": "st_stage_change", "args": {"descricao": "htop", "pacotes": ["htop"]}})
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["onde"], "ST")
        self.assertEqual(r["approved_by"], "política")          # livre: ensaio não pergunta
        # a IA tenta promover dizendo outra coisa: o executor usa a mudança REAL do ensaio
        t, box = self.run_async("st_promote", {"mudanca": {"pacotes": ["outra-coisa"]}})
        p = self.wait_pending()
        self.assertEqual(p["tool"], "st_promote")
        self.assertIn("htop", p["summary"])
        self.assertNotIn("outra-coisa", p["summary"])
        self.assertIn("htop: ∅ → 3.3.0", p["detalhes"])
        self.assertIn("Verificações no ST: OK", p["detalhes"])
        E.op_decide(UID, {"id": p["id"], "decision": "once"})
        t.join(5)
        r = box["r"]
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["onde"], "PD")
        self.assertTrue(r["can_undo"])
        self.assertEqual(os.path.realpath(self.root.profile), self.root.pd_top)
        u = E.op_undo(UID, {"action_id": r["action_id"]})
        self.assertTrue(u["ok"], u)
        self.assertEqual(os.path.realpath(self.root.profile), self.root.current)

    def test_promote_without_stage_answers_without_asking(self):
        r = E.op_run(UID, {"tool": "st_promote", "args": {}})
        self.assertEqual(r["status"], "error")
        self.assertIn("não há mudança em ensaio", r["reason"])
        self.assertFalse(E.op_status(UID)["pending"])

    def test_st_tools_on_live_iso(self):
        ST.LIVE = True
        for tool, args in [("st_stage_change", {"descricao": "x", "pacotes": ["htop"]}), ("st_diff", {}),
                           ("st_run", {"command": "ls"}), ("st_promote", {})]:
            with self.subTest(tool=tool):
                r = E.op_run(UID, {"tool": tool, "args": args})
                self.assertEqual(r["status"], "error")
                self.assertIn("indisponível no modo live", r["reason"])
        self.assertFalse(E.op_status(UID)["pending"])
        self.assertTrue(any(e.get("event") == "resultado" for e in self.events))   # ficou na auditoria

    def test_set_kernel_param_undo_restores_old_value(self):
        writes = []
        saved = (E.write_sysctl, E.read_sysctl, E.os.path.isfile)
        E.write_sysctl = lambda path, value, cats: (writes.append((path, value)) or 0,
                                                    {"parametro": path, "antes": "0", "agora": value})
        E.read_sysctl = lambda path: "0"
        E.RUNNERS["set_kernel_param"] = E.tool_set_kernel_param
        real_isfile = os.path.isfile
        E.os.path.isfile = lambda p: True if p.startswith("/proc/sys/") else real_isfile(p)
        try:
            self.mode = "perguntar"
            t, box = self.run_async("set_kernel_param", {"name": "net.ipv4.icmp_echo_ignore_all", "value": "1"})
            p = self.wait_pending()
            self.assertIn("valor atual '0' -> novo '1'", p["detalhes"])
            E.op_decide(UID, {"id": p["id"], "decision": "once"})
            t.join(5)
            r = box["r"]
            self.assertEqual(r["status"], "ok", r)
            self.assertTrue(r["result"]["temporario"])
            self.assertTrue(r["can_undo"])
            self.assertTrue(E.op_undo(UID, {"action_id": r["action_id"]})["ok"])
            self.assertEqual(writes, [("/proc/sys/net/ipv4/icmp_echo_ignore_all", "1"),
                                      ("/proc/sys/net/ipv4/icmp_echo_ignore_all", "0")])
        finally:
            E.write_sysctl, E.read_sysctl, E.os.path.isfile = saved


if __name__ == "__main__":
    unittest.main(verbosity=1)
