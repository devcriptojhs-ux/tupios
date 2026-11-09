"""Testes do classificador e da política (modo x categoria, invariantes, camadas)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tupi_agent import policy as P  # noqa: E402

HOME = "/home/nixos"

EXPECTED = {
    "restrito": {"read": "allow", "read_secret": "deny", "write_user_data": "ask", "write_system_config": "deny",
                 "install_package": "deny", "stage_change": "ask", "promote_change": "ask", "tune_kernel": "deny",
                 "network_egress": "ask", "execute_binary": "ask",
                 "modify_bootloader": "deny", "access_hardware": "deny", "privilege_escalation": "deny",
                 "delete_system_file": "deny", "control_desktop": "ask"},
    "perguntar": {"read": "ask", "read_secret": "deny", "write_user_data": "ask", "write_system_config": "ask",
                  "install_package": "ask", "stage_change": "ask", "promote_change": "ask", "tune_kernel": "ask",
                  "network_egress": "ask", "execute_binary": "ask",
                  "modify_bootloader": "deny", "access_hardware": "deny", "privilege_escalation": "deny",
                  "delete_system_file": "deny", "control_desktop": "ask"},
    "livre": {"read": "allow", "read_secret": "deny", "write_user_data": "allow", "write_system_config": "allow",
              "install_package": "allow", "stage_change": "allow", "promote_change": "ask", "tune_kernel": "allow",
              "network_egress": "allow", "execute_binary": "allow",
              "modify_bootloader": "allow", "access_hardware": "allow", "privilege_escalation": "allow",
              "delete_system_file": "allow", "control_desktop": "ask"},
}


def one(cat):
    c = P.Classification("teste", "teste")
    c.add(cat)
    return c


def cls(tool, **args):
    return P.classify(tool, args, HOME)


def cats(tool, **args):
    return sorted(cls(tool, **args).categories)


class ModeMatrix(unittest.TestCase):
    def test_every_mode_every_category(self):
        for mode, expected in EXPECTED.items():
            pol = P.effective_policy({"mode": mode}, {})
            for cat in P.CATEGORIES:
                with self.subTest(mode=mode, cat=cat):
                    decision, _ = P.decide(one(cat), pol)
                    self.assertEqual(decision, expected[cat])

    def test_all_categories_covered(self):
        for mode in P.MODES:
            self.assertEqual(set(EXPECTED[mode]), set(P.CATEGORIES))


class Layers(unittest.TestCase):
    def test_nix_override_then_runtime_override(self):
        pol = P.effective_policy({"mode": "restrito", "overrides": {"install_package": "ask"}},
                                 {"overrides": {"network_egress": "deny"}})
        self.assertEqual(pol["table"]["install_package"], "ask")
        self.assertEqual(pol["sources"]["install_package"], "NixOS (tupi.agent.policyOverrides)")
        self.assertEqual(pol["table"]["network_egress"], "deny")
        self.assertEqual(pol["sources"]["network_egress"], "ajuste em Permissões da IA")

    def test_runtime_mode_wins(self):
        pol = P.effective_policy({"mode": "restrito"}, {"mode": "perguntar"})
        self.assertEqual(pol["mode"], "perguntar")

    def test_livre_expires_after_deadline(self):
        rt = {"mode": "livre", "livre": {"scope": "1h", "until": 1000}}
        self.assertEqual(P.effective_policy({"mode": "restrito"}, rt, now=999)["mode"], "livre")
        pol = P.effective_policy({"mode": "restrito"}, rt, now=1001)
        self.assertEqual(pol["mode"], "restrito")
        self.assertTrue(pol["notes"])

    def test_livre_until_reboot(self):
        rt = {"mode": "livre", "livre": {"scope": "boot", "boot_id": "aaa"}}
        self.assertEqual(P.effective_policy({}, rt, boot_id="aaa")["mode"], "livre")
        self.assertEqual(P.effective_policy({}, rt, boot_id="bbb")["mode"], "restrito")

    def test_secret_only_explicit(self):
        pol = P.effective_policy({"mode": "livre"}, {})
        self.assertEqual(P.decide(cls("read_file", path="~/.ssh/id_ed25519"), pol)[0], "deny")
        pol = P.effective_policy({"mode": "livre"}, {"overrides": {"read_secret": "allow"}})
        self.assertEqual(P.decide(cls("read_file", path="~/.ssh/id_ed25519"), pol)[0], "allow")

    def test_critical_by_explicit_override(self):
        pol = P.effective_policy({"mode": "perguntar"}, {"overrides": {"delete_system_file": "ask"}})
        self.assertEqual(P.decide(cls("run_command", command="rm /etc/fstab"), pol)[0], "ask")


class Invariants(unittest.TestCase):
    def test_self_policy_denied_even_in_livre_with_overrides(self):
        pol = P.effective_policy({"mode": "livre"}, {"overrides": {c: "allow" for c in P.CATEGORIES}})
        for tool, args in [
            ("write_file", {"path": "/etc/tupi/agent-policy.json", "content": "{}"}),
            ("write_file", {"path": "/etc/tupi/permission-policy.json", "content": "{}"}),
            ("write_file", {"path": "/var/log/tupi-audit/permission.log", "content": ""}),
            ("run_command", {"command": "echo '{}' > /etc/tupi/agent-policy.json"}),
            ("run_command", {"command": "sudo systemctl stop tupi-perm-mgr"}),
            ("run_command", {"command": "rm -f /var/log/tupi-audit/permission.log"}),
            ("manage_service", {"name": "tupi-agent-exec", "action": "stop"}),
            ("manage_service", {"name": "tupi-perm-mgr", "action": "restart"}),
        ]:
            with self.subTest(tool=tool, args=args):
                self.assertEqual(P.decide(cls(tool, **args), pol)[0], "deny")

    def test_reading_policy_is_fine(self):
        pol = P.effective_policy({"mode": "restrito"}, {})
        self.assertEqual(P.decide(cls("run_command", command="cat /etc/tupi/agent-policy.json"), pol)[0], "allow")

    def test_destructive_asks_even_in_livre(self):
        pol = P.effective_policy({"mode": "livre"}, {})
        for cmd in ["rm -rf ~/projeto", "rm -r -f build", "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sdb1",
                    "sudo chmod -R 777 /", "find . -name '*.o' -delete", "git reset --hard", ":(){ :|:& };:",
                    "shred segredo.txt", "reboot"]:
            with self.subTest(cmd=cmd):
                c = cls("run_command", command=cmd)
                self.assertTrue(c.destructive, cmd)
                self.assertEqual(P.decide(c, pol)[0], "ask")

    def test_unknown_tool_denied(self):
        pol = P.effective_policy({"mode": "livre"}, {})
        self.assertEqual(P.decide(cls("format_disk"), pol)[0], "deny")


class Classifier(unittest.TestCase):
    def test_tools(self):
        self.assertEqual(cats("list_dir", path="~"), ["read"])
        self.assertEqual(cats("read_file", path="~/notas.txt"), ["read"])
        self.assertEqual(cats("read_file", path="/etc/tupi/ai-config.json"), ["read_secret"])
        self.assertEqual(cats("list_dir", path="~/.ssh"), ["read_secret"])
        self.assertEqual(cats("write_file", path="~/notas.txt"), ["write_user_data"])
        self.assertEqual(cats("write_file", path="/etc/hosts"), ["write_system_config"])
        self.assertEqual(cats("write_file", path="/boot/loader/loader.conf"), ["modify_bootloader"])
        self.assertEqual(cats("system_info"), ["read"])
        self.assertEqual(cats("take_screenshot"), ["read"])
        self.assertEqual(cats("open_app", app="firefox"), ["execute_binary"])
        self.assertEqual(cats("manage_service", name="sshd", action="status"), ["read"])
        self.assertEqual(cats("manage_service", name="sshd", action="restart"), ["write_system_config"])
        self.assertEqual(cats("install_package", name="hello"), ["install_package"])

    def test_commands(self):
        table = {
            "ls -la ~": ["read"],
            "df -h && free -m": ["read"],
            "cat ~/notas.txt | grep tupi | wc -l": ["read"],
            "ls ~ 2>&1 | head": ["read"],
            "rm /etc/fstab": ["delete_system_file"],
            "rm ~/lixo.txt": ["write_user_data"],
            "echo oi > ~/a.txt": ["write_user_data"],
            "echo oi > /etc/motd": ["write_system_config"],
            "curl -s https://exemplo.com": ["network_egress"],
            "curl -o ~/x.zip https://exemplo.com/x.zip": ["network_egress", "write_user_data"],
            "sudo ls /root": ["read_secret", "privilege_escalation"],
            "sudo apt install vim": ["install_package", "network_egress", "privilege_escalation"],
            "nix-env -iA nixpkgs.hello": ["install_package", "network_egress"],
            "dd if=x.iso of=/dev/sdb": ["access_hardware"],
            "grub-install /dev/sda": ["modify_bootloader"],
            "python3 script.py": ["execute_binary"],
            "./meu-programa": ["execute_binary"],
            "systemctl status sshd": ["read"],
            "systemctl restart sshd": ["write_system_config"],
            "bash -c 'rm /etc/passwd'": ["delete_system_file"],
            "cat $(ls)": ["execute_binary"],
            "git status": ["read"],
            "git clone https://x/y.git": ["network_egress", "write_user_data"],
            "cat ~/.ssh/id_ed25519": ["read_secret"],
            "cp /etc/shadow ~/": ["read_secret", "write_user_data"],
        }
        for cmd, expected in table.items():
            with self.subTest(cmd=cmd):
                got = sorted(cls("run_command", command=cmd).categories, key=P.CATEGORIES.index)
                self.assertEqual(got, sorted(expected, key=P.CATEGORIES.index))

    def test_spec_scenarios_in_restrito(self):
        pol = P.effective_policy({"mode": "restrito"}, {})
        self.assertEqual(P.decide(cls("list_dir", path="~"), pol)[0], "allow")
        self.assertEqual(P.decide(cls("run_command", command="rm /etc/fstab"), pol)[0], "deny")
        self.assertEqual(P.decide(cls("install_package", name="hello"), pol)[0], "deny")
        pol = P.effective_policy({"mode": "perguntar"}, {})
        self.assertEqual(P.decide(cls("install_package", name="hello"), pol)[0], "ask")
        self.assertEqual(P.decide(cls("run_command", command="rm /etc/fstab"), pol)[0], "deny")


class StagingAndKernel(unittest.TestCase):
    """ST (ensaio), promoção para o PD e parâmetros do kernel."""

    def pol(self, mode, overrides=None):
        return P.effective_policy({"mode": mode}, {"overrides": overrides or {}})

    def test_new_tools_categories_and_where(self):
        self.assertEqual(cats("st_run", command="systemctl status sshd"), ["stage_change"])
        self.assertEqual(cls("st_run", command="ls").where, "ST")
        self.assertEqual(cats("st_stage_change", pacotes=["htop"]), ["stage_change"])
        self.assertEqual(cls("st_stage_change", pacotes=["htop"]).where, "ST")
        self.assertEqual(cats("st_diff"), ["read"])
        self.assertEqual(cats("st_discard"), ["stage_change"])
        self.assertEqual(cats("st_promote", mudanca={"pacotes": ["htop"]}), ["promote_change"])
        self.assertEqual(cls("st_promote", mudanca={"pacotes": ["htop"]}).where, "PD")
        self.assertEqual(cats("set_kernel_param", name="vm.swappiness", value="10"), ["tune_kernel"])

    def test_promote_always_asks_even_in_livre_with_overrides(self):
        c = cls("st_promote", mudanca={"pacotes": ["htop"]})
        for mode in P.MODES:
            with self.subTest(mode=mode):
                self.assertEqual(P.decide(c, self.pol(mode, {"promote_change": "allow"}))[0], "ask")
        self.assertEqual(self.pol("livre", {"promote_change": "allow"})["table"]["promote_change"], "ask")
        self.assertEqual(P.decide(c, self.pol("livre", {"promote_change": "deny"}))[0], "deny")

    def test_stage_restrito_asks_livre_allows(self):
        c = cls("st_stage_change", pacotes=["htop"])
        self.assertEqual(P.decide(c, self.pol("restrito"))[0], "ask")
        self.assertEqual(P.decide(c, self.pol("perguntar"))[0], "ask")
        self.assertEqual(P.decide(c, self.pol("livre"))[0], "allow")

    def test_tune_kernel_by_mode(self):
        c = cls("set_kernel_param", name="net.ipv4.icmp_echo_ignore_all", value="1")
        self.assertFalse(c.destructive)
        self.assertEqual(P.decide(c, self.pol("restrito"))[0], "deny")
        self.assertEqual(P.decide(c, self.pol("perguntar"))[0], "ask")
        self.assertEqual(P.decide(c, self.pol("livre"))[0], "allow")

    def test_sysctl_common_params_are_normal(self):
        for name, value in [("vm.swappiness", "10"), ("net.ipv4.icmp_echo_ignore_all", "1"),
                            ("net.ipv4.ip_forward", "1"), ("vm.dirty_ratio", "15"), ("fs.inotify.max_user_watches",
                                                                                     "524288"),
                            ("kernel.printk", "4 4 1 7"), ("vm.overcommit_memory", "0"), ("vm.overcommit_ratio", "50")]:
            with self.subTest(name=name):
                c = cls("set_kernel_param", name=name, value=value)
                self.assertFalse(c.destructive or c.forbidden, (name, c.reasons, c.forbidden))
                self.assertEqual(P.decide(c, self.pol("livre"))[0], "allow")

    def test_sysctl_risky_params_need_extra_confirmation_even_in_livre(self):
        for name, value in [("kernel.panic", "10"), ("kernel.panic_on_oops", "1"), ("kernel.sysrq", "1"),
                            ("vm.overcommit_memory", "2"), ("vm.overcommit_memory", "1"), ("vm.overcommit_ratio", "500"),
                            ("vm.panic_on_oom", "1"), ("kernel.randomize_va_space", "0"),
                            ("fs.protected_symlinks", "0"), ("vm.min_free_kbytes", "1"),
                            ("kernel.yama.ptrace_scope", "0")]:
            with self.subTest(name=name, value=value):
                c = cls("set_kernel_param", name=name, value=value)
                self.assertTrue(c.destructive, name)
                self.assertFalse(c.forbidden)
                self.assertEqual(P.decide(c, self.pol("livre"))[0], "ask")
                self.assertEqual(P.decide(c, self.pol("restrito"))[0], "deny")

    def test_sysctl_forbidden_in_every_mode(self):
        every = self.pol("livre", {c: "allow" for c in P.CATEGORIES})
        for name, value in [("kernel.core_pattern", "|/tmp/x"), ("kernel.modprobe", "/tmp/x"),
                            ("kernel.modules_disabled", "1"), ("kernel.kexec_load_disabled", "1"),
                            ("kernel.usermodehelper.bset", "0 0"), ("kernel.poweroff_cmd", "/tmp/x"),
                            ("kernel.yama.ptrace_scope", "3"), ("vm/../../etc", "1"), ("vm.swappiness", "10\n0"),
                            ("vm.swappiness", "$(reboot)")]:
            with self.subTest(name=name, value=value):
                c = cls("set_kernel_param", name=name, value=value)
                self.assertTrue(c.forbidden, name)
                self.assertEqual(P.decide(c, every)[0], "deny")

    def test_run_command_sysctl_is_tune_kernel(self):
        self.assertEqual(cats("run_command", command="sysctl -w vm.swappiness=10"), ["tune_kernel"])
        self.assertEqual(cats("run_command", command="sudo sysctl vm.swappiness=10"),
                         ["privilege_escalation", "tune_kernel"])
        self.assertEqual(cats("run_command", command="echo 1 > /proc/sys/net/ipv4/ip_forward"), ["tune_kernel"])
        self.assertEqual(cats("run_command", command="sysctl vm.swappiness"), ["read"])
        self.assertEqual(cats("run_command", command="sysctl -a"), ["read"])
        self.assertTrue(cls("run_command", command="sysctl --system").destructive)
        self.assertTrue(cls("run_command", command="echo '|/x' > /proc/sys/kernel/core_pattern").forbidden)

    def test_st_run_detects_kernel_tuning(self):
        self.assertTrue(P.touches_kernel("sysctl -w vm.swappiness=10"))
        self.assertTrue(P.touches_kernel("echo 10 > /proc/sys/vm/swappiness"))
        self.assertFalse(P.touches_kernel("sysctl vm.swappiness"))
        self.assertFalse(P.touches_kernel("systemctl --failed"))
        self.assertTrue(any("mesmo kernel" in r for r in cls("st_run", command="sysctl -w vm.swappiness=10").reasons))

    def test_config_changes_touching_the_ai_are_denied(self):
        every = self.pol("livre", {c: "allow" for c in P.CATEGORIES})
        for nix in ['tupi.agent.mode = "livre";', 'systemd.services.tupi-perm-mgr.enable = false;',
                    'security.sudo.extraRules = [{ users = ["tupi-agent"]; commands = ["ALL"]; }];',
                    'environment.etc."tupi/agent-policy.json".text = "{}";',
                    'tupi.permissionManager.auditLogPath = "/dev/null";']:
            with self.subTest(nix=nix):
                self.assertEqual(P.decide(cls("st_stage_change", nix=nix), every)[0], "deny")
                self.assertEqual(P.decide(cls("st_promote", mudanca={"nix": nix}), every)[0], "deny")

    def test_promote_of_boot_or_risky_sysctl_needs_extra_confirmation(self):
        for change in [{"parametros_kernel": ["quiet"]}, {"nix": 'boot.loader.timeout = 1;'},
                       {"sysctl": {"kernel.panic": 5}}]:
            with self.subTest(change=change):
                c = cls("st_promote", mudanca=change)
                self.assertTrue(c.destructive)
                self.assertEqual(P.decide(c, self.pol("perguntar"))[0], "ask")
                # ensaiar no ST continua simples (não afeta o PD)
                self.assertFalse(cls("st_stage_change", **change).destructive)
        self.assertTrue(cls("st_promote", mudanca={"sysctl": {"kernel.core_pattern": "|/x"}}).forbidden)

    def test_sysctl_in_stage_explains_container_limit(self):
        c = cls("st_stage_change", sysctl={"vm.swappiness": 10})
        self.assertTrue(any("mesmo kernel" in r for r in c.reasons))

    def test_invalid_package_names_blocked(self):
        self.assertTrue(cls("st_stage_change", pacotes=["htop; rm -rf /"]).forbidden)
        self.assertFalse(cls("st_stage_change", pacotes=["htop", "python3Packages.requests"]).forbidden)


class ComputerUse(unittest.TestCase):
    """Usar o computador (mouse/teclado/abrir arquivos): assistido, nunca liberado sozinho."""

    def pol(self, mode, ov=None):
        return P.effective_policy({"mode": mode}, {"overrides": ov or {}})

    def test_look_is_read_act_is_control(self):
        self.assertEqual(cats("use_computer", action="screenshot"), ["read"])
        self.assertEqual(cats("use_computer", action="wait", seconds=2), ["read"])
        for action in ("click", "double_click", "right_click", "move", "scroll"):
            with self.subTest(action=action):
                self.assertEqual(cats("use_computer", action=action, x=10, y=20), ["control_desktop"])
        self.assertEqual(cats("use_computer", action="type", text="Olá, mundo"), ["control_desktop"])
        self.assertEqual(cats("use_computer", action="key", keys="ctrl+s"), ["control_desktop"])
        self.assertEqual(cats("open_file", path="~/Documentos/a.pdf"), ["control_desktop"])
        self.assertEqual(P.decide(cls("use_computer", action="screenshot"), self.pol("restrito"))[0], "allow")

    def test_always_asks_in_every_mode_even_with_allow_override(self):
        c = cls("use_computer", action="click", x=1, y=1)
        for mode in P.MODES:
            with self.subTest(mode=mode):
                self.assertEqual(P.decide(c, self.pol(mode, {"control_desktop": "allow"}))[0], "ask")
        pol = self.pol("livre", {"control_desktop": "allow"})
        self.assertEqual(pol["table"]["control_desktop"], "ask")
        self.assertTrue(any("sempre pergunta" in n for n in pol["notes"]))
        self.assertEqual(P.decide(c, self.pol("livre", {"control_desktop": "deny"}))[0], "deny")   # pode desligar

    def test_typed_text_is_judged_like_a_command(self):
        c = cls("use_computer", action="type", text="sudo rm -rf /etc\n")
        self.assertIn("privilege_escalation", c.categories)
        self.assertEqual(P.decide(c, self.pol("restrito"))[0], "deny")
        self.assertEqual(P.decide(c, self.pol("perguntar"))[0], "deny")
        c = cls("use_computer", action="type", text="rm -rf ~/projeto\n")
        self.assertTrue(c.destructive)
        self.assertEqual(P.decide(c, self.pol("livre"))[0], "ask")
        self.assertIn("read_secret", cls("use_computer", action="type", text="cat ~/.ssh/id_ed25519").categories)

    def test_ai_cannot_approve_itself_or_touch_policy_by_typing(self):
        for text in ("tupi-agent approve 1a2b3c", "tupi-agent policy mode livre", "nano /etc/tupi/agent-policy.json",
                     "paje provedor revisor --chave x"):
            with self.subTest(text=text):
                c = cls("use_computer", action="type", text=text)
                self.assertTrue(c.self_policy)
                self.assertEqual(P.decide(c, self.pol("livre"))[0], "deny")

    def test_forbidden_keys_and_bad_args(self):
        for keys in ("ctrl+alt+F2", "ctrl+alt+BackSpace", "ctrl+alt+Delete", "alt+ctrl+F3"):
            with self.subTest(keys=keys):
                self.assertTrue(cls("use_computer", action="key", keys=keys).forbidden)
        self.assertFalse(cls("use_computer", action="key", keys="ctrl+l Return").forbidden)
        self.assertTrue(cls("use_computer", action="key", keys="ctrl+s; rm").forbidden)
        self.assertTrue(cls("use_computer", action="click").forbidden)              # sem coordenadas
        self.assertTrue(cls("use_computer", action="drag", x=1, y=1).forbidden)      # sem destino
        self.assertTrue(cls("use_computer", action="voar").forbidden)
        self.assertEqual(P.decide(cls("use_computer", action="voar"), self.pol("livre"))[0], "deny")

    def test_open_secret_file_is_denied(self):
        c = cls("open_file", path="~/.ssh/id_rsa")
        self.assertIn("read_secret", c.categories)
        self.assertEqual(P.decide(c, self.pol("livre"))[0], "deny")


if __name__ == "__main__":
    unittest.main(verbosity=1)
