"""Conexões nomeadas do tupi-ai-router (cada subagente do Pajé pode usar uma), com servidores falsos.

Uso: TUPI_TEST_ROUTER=http://127.0.0.1:PORTA TUPI_TEST_LOG=<log do fake_llm> python3 -m unittest ...
O router deve estar com: principal = OpenAI falsa (agente-roteirizado) e conexoes.claude = Anthropic falsa.
"""
import json
import os
import unittest
import urllib.error
import urllib.request

ROUTER = os.environ.get("TUPI_TEST_ROUTER", "http://127.0.0.1:18081")
LOG = os.environ.get("TUPI_TEST_LOG", "/tmp/fake_llm.log")


def call(path, body=None):
    req = urllib.request.Request(ROUTER + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def last(api):
    with open(LOG) as f:
        lines = [json.loads(x) for x in f if x.strip()]
    return [x for x in lines if x["api"] == api and x["body"]][-1]["body"]


class Conexoes(unittest.TestCase):
    def test_health_lists_connections_without_keys(self):
        h = call("/health")
        names = {c["nome"]: c for c in h["conexoes"]}
        self.assertEqual(set(names), {"principal", "claude"})
        self.assertEqual(names["claude"]["provider"], "anthropic")
        self.assertTrue(names["claude"]["configured"])
        self.assertNotIn("api_key", json.dumps(h))

    def test_principal_goes_to_openai(self):
        r = call("/v1/chat/completions", {"messages": [{"role": "user", "content": "oi"}]})
        self.assertIn("agente roteirizado", r["choices"][0]["message"]["content"])
        self.assertEqual(r["model"], "agente-roteirizado")
        self.assertNotIn("tupi_conexao", json.dumps(last("openai")))

    def test_named_connection_goes_to_anthropic_and_flag_is_not_forwarded(self):
        r = call("/v1/chat/completions", {"tupi_conexao": "claude", "messages": [
            {"role": "system", "content": "Você é o Pesquisador."}, {"role": "user", "content": "oi"}]})
        self.assertEqual(r["choices"][0]["message"]["role"], "assistant")
        sent = last("anthropic")
        self.assertEqual(sent["model"], "claude-opus-5-5")             # modelo padrão da conexão
        self.assertNotIn("tupi_conexao", json.dumps(sent))
        self.assertIn("Pesquisador", json.dumps(sent["system"]))

    def test_model_override_per_request(self):
        call("/v1/chat/completions", {"tupi_conexao": "claude", "model": "claude-sonnet-5-5",
                                      "messages": [{"role": "user", "content": "oi"}]})
        self.assertEqual(last("anthropic")["model"], "claude-sonnet-5-5")

    def test_models_of_a_connection(self):
        ids = [m["id"] for m in call("/v1/models?conexao=principal")["data"]]
        self.assertIn("agente-roteirizado", ids)

    def test_unknown_connection_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            call("/v1/chat/completions", {"tupi_conexao": "nao-existe", "messages": [{"role": "user", "content": "x"}]})
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main(verbosity=1)
