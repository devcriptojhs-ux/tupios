"""Almas (.soul.md) do Pajé e dos subagentes + a constituição fixa.

Cada agente é um arquivo Markdown com um cabeçalho simples (entre linhas ---) e o texto que descreve
o comportamento dele. O usuário pode editar à vontade (no app ou em qualquer editor):

    ---
    nome: Pesquisador
    papel: Procura e resume informações nos arquivos do usuário
    conexao: principal            # conexão do AI Router (Configurar IA / Harness)
    modelo:                       # vazio = modelo padrão da conexão
    ferramentas: list_dir, read_file, system_info
    orcamento_tokens_dia: 100000
    rotina_minutos: 0             # 0 = sem rotina; N = o Pajé cria um trabalho a cada N minutos
    rotina:                       # instrução do trabalho de rotina
    criado_por: usuario           # usuario | paje
    ---
    # Quem eu sou
    ...

A CONSTITUIÇÃO (regras de permissão, PD/ST/SEC, limites) vem sempre ANTES da alma e não é editável:
uma alma descreve personalidade e método de trabalho, nunca dá permissões. As permissões continuam no
Permission Manager, para o Pajé e para todos os subagentes.
"""
import os
import re
import time
import unicodedata

from .tools import AGENT_PROMPT, TOOL_SCHEMAS

DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "tupios-ia", "paje")

# Ferramentas que um subagente PODE receber (as do sistema, menos promover/descartar/instalar direto:
# aplicar no PD é decisão do Pajé, e sempre com aprovação do usuário)
SUB_TOOLS = ["list_dir", "read_file", "system_info", "write_file", "run_command", "take_screenshot", "open_app",
             "manage_service", "set_kernel_param", "st_run", "st_stage_change", "st_diff", "use_computer",
             "open_file"]
DEFAULT_SUB_TOOLS = ["list_dir", "read_file", "system_info"]
FIELDS = ("nome", "papel", "conexao", "modelo", "ferramentas", "orcamento_tokens_dia", "rotina_minutos", "rotina",
          "criado_por", "criado_em")
DEFAULT_BUDGET = int(os.environ.get("TUPI_PAJE_BUDGET", "100000"))
NAME_RE = re.compile(r"^[\wÀ-ÿ][\wÀ-ÿ .'-]{0,39}$")

CONSTITUICAO_PAJE = AGENT_PROMPT + (
    "\n\nOrquestração (você é o Pajé e coordena subagentes):\n"
    "- Você pode ter subagentes: cada um tem nome, papel, alma (.soul.md), ferramentas e modelo próprios. Veja "
    "quem existe com listar_agentes.\n"
    "- Para trabalhos que se dividem em partes independentes, use delegar_tarefas (as partes rodam em PARALELO) e "
    "depois aguardar_tarefas para juntar os resultados e responder ao usuário com uma síntese.\n"
    "- Se nenhum subagente serve para a tarefa, você PODE criar um com criar_subagente: dê um nome claro, um "
    "papel, uma alma curta e prescritiva e só as ferramentas necessárias (mínimo privilégio). Avise o usuário.\n"
    "- Subagentes respondem a você, não ao usuário. Você é responsável pelo resultado final: confira o que eles "
    "trouxeram antes de afirmar algo.\n"
    "- Subagentes nunca aplicam mudanças no PD: se um trabalho exigir isso, faça você mesmo pelo fluxo do ST "
    "(st_stage_change -> st_diff -> st_promote), que sempre pede aprovação ao usuário.\n"
    "- Não delegue o que é simples e rápido de fazer você mesmo.")

CONSTITUICAO_SUB = (
    "Você é {nome}, um SUBAGENTE do Pajé (a IA do sistema operacional TUPIOS, baseado em NixOS). Responda em "
    "português do Brasil.\n"
    "Regras fixas (valem acima da sua alma):\n"
    "- Você recebe UMA tarefa do Pajé e trabalha só nela. Ao terminar, responda com um relatório objetivo: o que "
    "fez, o que encontrou e o resultado. Esse relatório vai para o Pajé.\n"
    "- Toda ação passa pelo gerenciador de permissões do TUPIOS: algumas são liberadas, outras pedem aprovação do "
    "usuário e outras são negadas. Se algo for NEGADO, não tente contornar: diga no relatório o que foi negado.\n"
    "- Nunca tente alterar a política de permissões, o log de auditoria, o executor ou a chave da API.\n"
    "- Você não cria agentes, não delega e não aplica mudanças no PD (o sistema real). Mudanças de sistema podem "
    "ser ENSAIADAS no ST (st_stage_change, st_diff) se você tiver essas ferramentas; aplicar é com o Pajé.\n"
    "- O ST é um container com o mesmo kernel do PD: parâmetros do kernel não são ensaiados nele.\n"
    "- Mouse e teclado (use_computer) são assistidos: o usuário acompanha; comece com screenshot, vá em passos "
    "curtos e, se o controle voltar para o usuário ou outro agente estiver usando a tela, pare e diga isso no "
    "relatório. Nunca digite senhas.\n"
    "- Só afirme o que as ferramentas confirmaram.")

PAJE_SOUL_DEFAULT = """---
nome: Pajé
papel: IA principal do TUPIOS; conversa com você e coordena os subagentes
conexao: principal
modelo:
---
# Quem eu sou
Sou o Pajé, a IA do TUPIOS. Converso com você, cuido do sistema com cuidado e coordeno uma equipe de
subagentes quando um trabalho pode ser dividido.

# Como eu trabalho
- Entendo o pedido e, se for grande, divido em partes que podem rodar em paralelo.
- Escolho o subagente certo para cada parte (ou crio um, se faltar alguém) e depois junto os resultados.
- Explico o plano antes de mudanças grandes e mostro o que mudou ao final.

# Meu jeito
- Direto, claro e honesto: digo o que não consegui fazer e por quê.
"""

TEMPLATES = {
    "pesquisador": ("Pesquisador", "Procura informações nos seus arquivos e no sistema e entrega um resumo",
                    ["list_dir", "read_file", "system_info"],
                    "# Quem eu sou\nSou o Pesquisador. Encontro informações e trago resumos curtos e confiáveis.\n\n"
                    "# Como trabalho\n- Procuro primeiro nos lugares mais prováveis.\n- Cito de onde tirei cada "
                    "informação (arquivo, comando).\n- Não altero nada: só leio.\n"),
    "analista": ("Analista do Sistema", "Verifica o estado do computador (desempenho, disco, serviços) e aponta problemas",
                 ["system_info", "run_command", "read_file", "list_dir"],
                 "# Quem eu sou\nSou o Analista do Sistema. Olho o computador como um médico olha um exame.\n\n"
                 "# Como trabalho\n- Uso comandos de leitura (df, free, systemctl --failed, journalctl).\n- Explico o "
                 "que é normal e o que merece atenção.\n- Não mudo configurações.\n"),
    "escritor": ("Escritor", "Escreve e revisa textos e salva arquivos na sua pasta",
                 ["read_file", "write_file", "list_dir"],
                 "# Quem eu sou\nSou o Escritor. Produzo textos claros em português.\n\n# Como trabalho\n- Leio o "
                 "material de referência antes.\n- Salvo o texto pronto onde o Pajé pedir.\n"),
    "operador": ("Operador", "Usa programas gráficos por você: abre arquivos, clica e digita, com você acompanhando",
                 ["use_computer", "open_file", "open_app", "list_dir", "read_file"],
                 "# Quem eu sou\nSou o Operador. Uso o computador como uma pessoa: vejo a tela, mexo o mouse e "
                 "digito.\n\n# Como trabalho\n- Começo sempre olhando a tela (screenshot).\n- Um passo de cada vez, "
                 "conferindo com outro screenshot.\n- Prefiro atalhos de teclado conhecidos.\n- Se você mexer no "
                 "mouse, paro e aviso o Pajé.\n\n# O que não faço\n- Não digito senhas.\n- Não clico em nada que "
                 "pague, apague ou envie sem que a tarefa peça.\n"),
}


# ------------------------------------------------------------------ util
def slugify(name):
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return (s or "agente")[:40]


def agents_dir():
    return os.path.join(DATA, "agentes")


def paje_soul_path():
    return os.path.join(DATA, "paje.soul.md")


def soul_path(agent_id):
    if not re.fullmatch(r"[a-z0-9-]{1,40}", agent_id or ""):
        raise ValueError("identificador de agente inválido")
    return os.path.join(agents_dir(), agent_id + ".soul.md")


def parse(text):
    """Texto .soul.md -> (campos, corpo)."""
    meta, body = {}, text or ""
    m = re.match(r"\s*---\s*\n(.*?)\n---\s*\n?(.*)\Z", text or "", re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            line = re.sub(r"\s{2,}#.*$", "", line).rstrip()     # comentário no fim da linha ("   # ...")
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    return meta, body.strip() + "\n"


def serialize(meta, body):
    lines = ["---"]
    for k in FIELDS:
        if k in meta:
            v = meta[k]
            if isinstance(v, (list, tuple)):
                v = ", ".join(v)
            lines.append(f"{k}: {'' if v is None else v}")
    lines += ["---", "", (body or "").strip(), ""]
    return "\n".join(lines)


def clean_name(name):
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not NAME_RE.match(name):
        raise ValueError("nome inválido: use até 40 letras, números, espaço, ponto, hífen ou apóstrofo")
    if slugify(name) == "paje":
        raise ValueError("\"Pajé\" é o nome da IA principal; escolha outro nome para o subagente")
    return name


def normalize_agent(meta, body, agent_id=None):
    """Valida e completa os campos. Nunca concede ferramenta fora de SUB_TOOLS."""
    nome = clean_name(meta.get("nome") or agent_id or "")
    tools = meta.get("ferramentas")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    tools = [t for t in (tools if tools is not None else DEFAULT_SUB_TOOLS) if t in SUB_TOOLS]
    def num(key, default, lo, hi):
        try:
            return max(lo, min(hi, int(str(meta.get(key, default)).strip() or default)))
        except ValueError:
            return default
    conexao = re.sub(r"[^\w.-]", "", str(meta.get("conexao") or "principal"))[:40] or "principal"
    modelo = re.sub(r"[^\w.:/@+-]", "", str(meta.get("modelo") or ""))[:100]
    return {"id": agent_id or slugify(nome), "nome": nome, "papel": str(meta.get("papel") or "")[:300],
            "conexao": conexao, "modelo": modelo, "ferramentas": list(dict.fromkeys(tools)),
            "orcamento_tokens_dia": num("orcamento_tokens_dia", DEFAULT_BUDGET, 1000, 10_000_000),
            "rotina_minutos": num("rotina_minutos", 0, 0, 10080),
            "rotina": str(meta.get("rotina") or "")[:1000],
            "criado_por": "paje" if str(meta.get("criado_por")) == "paje" else "usuario",
            "criado_em": str(meta.get("criado_em") or time.strftime("%Y-%m-%dT%H:%M")),
            "alma": (body or "").strip()[:20000]}


# --------------------------------------------------------------- arquivos
def list_agents():
    out = []
    try:
        names = sorted(os.listdir(agents_dir()))
    except OSError:
        return out
    for f in names:
        if f.endswith(".soul.md"):
            try:
                out.append(load_agent(f[:-len(".soul.md")]))
            except (OSError, ValueError):
                continue
    return out


def load_agent(agent_id):
    with open(soul_path(agent_id), encoding="utf-8") as f:
        meta, body = parse(f.read())
    a = normalize_agent(meta, body, agent_id)
    a["arquivo"] = soul_path(agent_id)
    return a


def save_agent(a):
    os.makedirs(agents_dir(), exist_ok=True)
    path = soul_path(a["id"])
    meta = {k: a.get(k) for k in FIELDS}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(serialize(meta, a.get("alma") or ""))
    os.replace(tmp, path)
    a["arquivo"] = path
    return a


def delete_agent(agent_id):
    os.remove(soul_path(agent_id))


def load_paje_soul():
    try:
        with open(paje_soul_path(), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = PAJE_SOUL_DEFAULT
    meta, body = parse(text)
    return {"nome": "Pajé", "conexao": re.sub(r"[^\w.-]", "", str(meta.get("conexao") or "principal"))[:40] or "principal",
            "modelo": re.sub(r"[^\w.:/@+-]", "", str(meta.get("modelo") or ""))[:100], "alma": body.strip(),
            "texto": text, "arquivo": paje_soul_path()}


def save_paje_soul(text):
    if len(text or "") > 30000:
        raise ValueError("alma grande demais")
    meta, body = parse(text)
    if not meta:      # só o corpo: mantém o cabeçalho
        text = PAJE_SOUL_DEFAULT.split("---\n# ", 1)[0] + "---\n" + (text or "").strip() + "\n"
    os.makedirs(DATA, exist_ok=True)
    with open(paje_soul_path(), "w", encoding="utf-8") as f:
        f.write(text)
    return load_paje_soul()


def sub_system_prompt(a):
    return CONSTITUICAO_SUB.format(nome=a["nome"]) + f"\n\nSeu papel: {a['papel']}\n\n# Sua alma\n{a['alma']}"


def paje_system_prompt(agents=None):
    soul = load_paje_soul()
    team = agents if agents is not None else list_agents()
    lines = [f"- {a['nome']} (id {a['id']}): {a['papel']} | ferramentas: {', '.join(a['ferramentas']) or 'nenhuma'}"
             for a in team]
    return (CONSTITUICAO_PAJE + "\n\n# Sua alma (escrita pelo usuário)\n" + soul["alma"] +
            "\n\n# Sua equipe agora\n" + ("\n".join(lines) or "(nenhum subagente ainda)"))


def sub_tool_schemas(a):
    allowed = set(a["ferramentas"])
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed]
