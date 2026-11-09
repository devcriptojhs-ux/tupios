"""Provedor próprio de cada subagente (conexão "ag-<id>" em ai-config.json): chave protegida, nunca reaproveitada
para outro endereço, removida junto com o agente."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tupi_agent import conexoes as C  # noqa: E402

TMP = tempfile.mkdtemp(prefix="tupi-conexoes-")
C.AI_CONFIG = os.path.join(TMP, "ai-config.json")


def cfg():
    with open(C.AI_CONFIG) as f:
        return json.load(f)


class AgentProvider(unittest.TestCase):
    def setUp(self):
        with open(C.AI_CONFIG, "w") as f:
            json.dump({"provider": "openai", "api_key": "principal-key", "model": "m0"}, f)

    def test_create_keep_key_and_never_send_it_elsewhere(self):
        name, model = C.set_agent_provider("revisor", {"preset": "custom", "base_url": "http://a.local/v1/",
                                                       "api_key": "k1", "model": "modelo-a"})
        self.assertEqual((name, model), ("ag-revisor", "modelo-a"))
        c = cfg()["conexoes"]["ag-revisor"]
        self.assertEqual((c["provider"], c["base_url"], c["api_key"], c["model"]), ("openai", "http://a.local/v1",
                                                                                   "k1", "modelo-a"))
        self.assertEqual(cfg()["api_key"], "principal-key")           # a conexão principal não muda
        # chave em branco, mesmo provedor e URL: mantém a salva (troca só o modelo)
        C.set_agent_provider("revisor", {"preset": "custom", "base_url": "http://a.local/v1", "model": "modelo-b"})
        self.assertEqual(cfg()["conexoes"]["ag-revisor"]["api_key"], "k1")
        # URL nova com a chave em branco: NÃO reaproveita a chave antiga
        with self.assertRaises(C.ConfigError):
            C.set_agent_provider("revisor", {"preset": "custom", "base_url": "http://outro.local/v1", "model": "x"})
        self.assertEqual(cfg()["conexoes"]["ag-revisor"]["base_url"], "http://a.local/v1")
        pub = C.agent_provider_public("revisor")
        self.assertEqual(pub, {"conexao": "ag-revisor", "preset": "custom", "base_url": "http://a.local/v1",
                               "model": "modelo-b", "tem_chave": True})
        self.assertNotIn("k1", json.dumps(pub))
        self.assertTrue(C.remove_agent_provider("revisor"))
        self.assertNotIn("ag-revisor", cfg().get("conexoes", {}))

    def test_validation(self):
        with self.assertRaises(C.ConfigError):
            C.set_agent_provider("x", {"preset": "nao-existe"})
        with self.assertRaises(C.ConfigError):                       # URL obrigatória nesse provedor
            C.set_agent_provider("x", {"preset": "custom", "api_key": "k"})
        with self.assertRaises(C.ConfigError):                       # URL que não é http(s)
            C.set_agent_provider("x", {"preset": "custom", "base_url": "file:///etc/passwd", "api_key": "k"})
        name, _ = C.set_agent_provider("Local Já", {"preset": "ollama"})   # sem chave: ok
        self.assertEqual(name, "ag-local-j-")
        self.assertTrue(C.NAME_RE.match(C.agent_conn_name("a" * 60)))


if __name__ == "__main__":
    unittest.main(verbosity=1)
