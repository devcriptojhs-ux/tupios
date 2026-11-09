"""Tradução de tool calling OpenAI <-> Anthropic no tupi-ai-router (com servidor falso).

Uso: TUPI_TEST_ROUTER=http://127.0.0.1:PORTA TUPI_TEST_LOG=<log do fake_llm> python3 -m unittest ...
O router deve estar configurado com provider "anthropic" apontando para o fake_llm.
"""
import json
import os
import unittest
import urllib.request

ROUTER = os.environ.get("TUPI_TEST_ROUTER", "http://127.0.0.1:18080")
LOG = os.environ.get("TUPI_TEST_LOG", "/tmp/fake_llm.log")

TOOLS = [
    {"type": "function", "function": {"name": "list_dir", "description": "Lista uma pasta",
                                      "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                                                     "required": ["path"]}}},
    {"type": "function", "function": {"name": "system_info", "description": "Info do sistema",
                                      "parameters": {"type": "object", "properties": {}}}},
]


def chat(body):
    req = urllib.request.Request(ROUTER + "/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def last_sent():
    with open(LOG) as f:
        lines = [json.loads(x) for x in f if x.strip()]
    return [x for x in lines if x["api"] == "anthropic" and x["body"]][-1]["body"]


class ToolTranslation(unittest.TestCase):
    def test_round_trip(self):
        msgs = [{"role": "system", "content": "Você é um agente."},
                {"role": "user", "content": "liste minha pasta"}]
        r = chat({"messages": msgs, "tools": TOOLS, "tool_choice": "auto"})
        choice = r["choices"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        msg = choice["message"]
        self.assertEqual(msg["tool_calls"][0]["id"], "toolu_A1")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "list_dir")
        self.assertEqual(json.loads(msg["tool_calls"][0]["function"]["arguments"]), {"path": "~"})
        self.assertEqual([b["type"] for b in msg["tupi_raw"]], ["thinking", "text", "tool_use"])
        sent = last_sent()
        self.assertEqual(sent["tools"][0]["name"], "list_dir")
        self.assertIn("input_schema", sent["tools"][0])
        self.assertEqual(sent["tool_choice"], {"type": "auto"})
        self.assertEqual(sent["system"], "Você é um agente.")

        # 2ª chamada: devolve a resposta inteira + o resultado da ferramenta
        msgs += [msg, {"role": "tool", "tool_call_id": "toolu_A1", "content": "a.txt\nb.txt"}]
        r2 = chat({"messages": msgs, "tools": TOOLS})
        self.assertEqual(r2["choices"][0]["finish_reason"], "stop")
        self.assertIn("Resultado recebido (1)", r2["choices"][0]["message"]["content"])
        sent = last_sent()
        asst, tool_msg = sent["messages"][1], sent["messages"][2]
        self.assertEqual(asst["role"], "assistant")
        self.assertEqual(asst["content"][0], {"type": "thinking", "thinking": "", "signature": "sig-123"})
        self.assertEqual(asst["content"][2]["type"], "tool_use")
        self.assertEqual(tool_msg["role"], "user")
        self.assertEqual(tool_msg["content"], [{"type": "tool_result", "tool_use_id": "toolu_A1",
                                                "content": "a.txt\nb.txt"}])

    def test_two_results_in_one_user_message_and_error_flag(self):
        msgs = [{"role": "user", "content": "faça DUAS coisas"}]
        msg = chat({"messages": msgs, "tools": TOOLS})["choices"][0]["message"]
        self.assertEqual(len(msg["tool_calls"]), 2)
        msgs += [msg,
                 {"role": "tool", "tool_call_id": "toolu_A1", "content": "ok"},
                 {"role": "tool", "tool_call_id": "toolu_A2", "content": "negado", "tupi_is_error": True},
                 {"role": "user", "content": [{"type": "text", "text": "e agora?"}]}]
        chat({"messages": msgs, "tools": TOOLS})
        sent = last_sent()
        self.assertEqual(len(sent["messages"]), 3, sent["messages"])
        blocks = sent["messages"][2]["content"]
        self.assertEqual([b["type"] for b in blocks], ["tool_result", "tool_result", "text"])
        self.assertTrue(blocks[1]["is_error"])

    def test_forced_choice_becomes_auto_and_parallel_flag(self):
        chat({"messages": [{"role": "user", "content": "liste"}], "tools": TOOLS,
              "tool_choice": {"type": "function", "function": {"name": "list_dir"}}, "parallel_tool_calls": False})
        self.assertEqual(last_sent()["tool_choice"], {"type": "auto", "disable_parallel_tool_use": True})
        chat({"messages": [{"role": "user", "content": "liste"}], "tools": TOOLS, "tool_choice": "none"})
        self.assertEqual(last_sent()["tool_choice"], {"type": "none"})

    def test_refusal_drops_partial_tool_call(self):
        r = chat({"messages": [{"role": "user", "content": "RECUSE isto"}], "tools": TOOLS})
        msg = r["choices"][0]["message"]
        self.assertNotIn("tool_calls", msg)
        self.assertEqual(r["choices"][0]["finish_reason"], "content_filter")
        self.assertIn("recusou", msg["content"])

    def test_plain_assistant_tool_calls_without_raw(self):
        # Histórico vindo de outro cliente (sem tupi_raw): vira tool_use normal
        msgs = [{"role": "user", "content": "liste"},
                {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_9", "type": "function", "function": {"name": "list_dir", "arguments": "{\"path\": \"/\"}"}}]},
                {"role": "tool", "tool_call_id": "call_9", "content": "bin etc"}]
        chat({"messages": msgs, "tools": TOOLS})
        sent = last_sent()
        self.assertEqual(sent["messages"][1]["content"],
                         [{"type": "tool_use", "id": "call_9", "name": "list_dir", "input": {"path": "/"}}])


if __name__ == "__main__":
    unittest.main(verbosity=1)
