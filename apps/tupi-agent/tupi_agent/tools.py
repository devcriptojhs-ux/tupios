"""Ferramentas do agente (formato OpenAI) e cliente do executor — usados pelo app e pelo modo proativo."""
import json
import os
import socket

EXEC_SOCKET = os.environ.get("TUPI_EXEC_SOCKET", "/run/tupi-agent/exec.sock")
PERM_SOCKET = os.environ.get("TUPI_PERM_SOCKET", "/run/tupi-perm-mgr/perm-mgr.sock")


def _fn(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}}


TOOL_SCHEMAS = [
    _fn("list_dir", "Lista os arquivos e pastas de um diretório. Use quando o usuário pedir para ver, listar ou "
        "procurar arquivos ('o que tem na minha pasta', 'liste meus documentos'). '~' é a pasta pessoal.",
        {"path": {"type": "string", "description": "Pasta, ex.: ~, ~/Documentos, /etc"}}, ["path"]),
    _fn("read_file", "Lê o conteúdo de um arquivo de texto. Use quando precisar ver o que um arquivo contém.",
        {"path": {"type": "string"}, "max_bytes": {"type": "integer", "description": "Padrão 20000"}}, ["path"]),
    _fn("write_file", "Cria ou altera um arquivo de texto (o sistema guarda uma cópia para Desfazer). Use para "
        "salvar, criar ou editar arquivos a pedido do usuário.",
        {"path": {"type": "string"}, "content": {"type": "string"},
         "mode": {"type": "string", "enum": ["overwrite", "append"], "description": "Padrão overwrite"}},
        ["path", "content"]),
    _fn("run_command", "Executa um comando de shell (bash) como o usuário, isolado, com limite de tempo. Use para "
        "tarefas que pedem um comando. Prefira as ferramentas específicas (list_dir, read_file, system_info, "
        "manage_service, install_package) quando existirem.",
        {"command": {"type": "string"}, "cwd": {"type": "string", "description": "Pasta de trabalho (padrão ~)"},
         "timeout": {"type": "integer", "description": "Segundos (padrão 60, máximo 600)"}}, ["command"]),
    _fn("system_info", "Mostra CPU, memória, disco, os processos que mais consomem e se o ST está disponível "
        "(e por quê, se não estiver). Use quando o usuário perguntar sobre desempenho, espaço ou memória, e "
        "ANTES de propor mudanças de sistema (para saber se dá para ensaiar no ST)."),
    _fn("manage_service", "Consulta ou controla um serviço do sistema (systemd).",
        {"name": {"type": "string", "description": "Ex.: sshd, NetworkManager"},
         "action": {"type": "string", "enum": ["status", "start", "stop", "restart"]}}, ["name", "action"]),
    _fn("install_package", "Instala um programa direto no PD (configuração NixOS, com ponto de retorno; nunca "
        "nix-env). Só funciona quando o ST NÃO está disponível; com o ST disponível use st_stage_change.",
        {"name": {"type": "string", "description": "Nome do pacote no nixpkgs, ex.: vlc, gimp"}}, ["name"]),
    _fn("st_stage_change", "Ensaia uma mudança de configuração do sistema no ST: aplica numa cópia da "
        "configuração NixOS, constrói, liga o ST com ela e verifica (unidades com falha novas, serviço alvo, "
        "programas novos). NÃO muda o PD. Use para instalar/remover programas, ligar serviços, mudar opções do "
        "NixOS ou tornar permanente um parâmetro do kernel. Mudanças se acumulam até st_promote ou st_discard.",
        {"descricao": {"type": "string", "description": "O que a mudança faz, em português"},
         "pacotes": {"type": "array", "items": {"type": "string"}, "description": "Pacotes do nixpkgs a instalar"},
         "remover_pacotes": {"type": "array", "items": {"type": "string"},
                             "description": "Pacotes instalados antes pela IA a remover"},
         "sysctl": {"type": "object", "description": "Parâmetros do kernel permanentes, ex.: {\"vm.swappiness\": 10}"},
         "parametros_kernel": {"type": "array", "items": {"type": "string"},
                               "description": "Parâmetros de boot do kernel (boot.kernelParams)"},
         "nix": {"type": "string", "description": "Trecho de módulo NixOS (corpo do attrset), ex.: "
                                                  "'services.openssh.enable = true;'"},
         "servico_alvo": {"type": "string", "description": "Serviço que deve ficar ativo, para verificar"}},
        ["descricao"]),
    _fn("st_diff", "Mostra o que muda entre o PD e o ensaio do ST: arquivos de configuração, pacotes (versões "
        "que entram/saem), serviços, sysctl e parâmetros de boot, além do resultado das verificações."),
    _fn("st_promote", "Aplica no PD (sistema real) a mudança JÁ ensaiada no ST: cria ponto de retorno "
        "(geração NixOS anterior + snapshot Btrfs), troca o sistema e oferece Desfazer. SEMPRE pede aprovação ao "
        "usuário, em qualquer modo, mostrando o diff. Só use depois de st_stage_change e de mostrar o st_diff."),
    _fn("st_discard", "Descarta a mudança em ensaio no ST (desliga o ST; o PD não muda)."),
    _fn("st_run", "Executa um comando de shell DENTRO do ST (container de ensaio, sem a pasta pessoal real e "
        "sem rede). Use para investigar ou testar algo no ST sem afetar o PD. Não serve para parâmetros do "
        "kernel (o ST usa o mesmo kernel do PD).",
        {"command": {"type": "string"}, "timeout": {"type": "integer", "description": "Segundos (padrão 60)"}},
        ["command"]),
    _fn("set_kernel_param", "Muda AGORA um parâmetro do kernel (sysctl) no PD, de forma temporária: vale até "
        "reiniciar, guarda o valor anterior e oferece Desfazer. Parâmetros perigosos pedem confirmação extra ou "
        "são bloqueados. Para tornar permanente, depois use st_stage_change com sysctl e st_promote.",
        {"name": {"type": "string", "description": "Ex.: vm.swappiness, net.ipv4.icmp_echo_ignore_all"},
         "value": {"type": "string", "description": "Novo valor, ex.: 10"}}, ["name", "value"]),
    _fn("open_app", "Abre um aplicativo gráfico para o usuário.",
        {"app": {"type": "string", "description": "Ex.: firefox, thunar, mousepad, xfce4-terminal"}}, ["app"]),
    _fn("take_screenshot", "Tira um print da tela do usuário para você ver o que está aparecendo. Use quando o "
        "usuário pedir para olhar a tela dele."),
    _fn("use_computer", "Usa o computador como uma pessoa, com o usuário acompanhando: vê a tela, mexe o mouse, "
        "clica, rola, arrasta, digita e aperta teclas. Fluxo: comece SEMPRE com action=screenshot (você recebe a "
        "imagem da tela); x e y são pixels NESSA imagem (o sistema converte para a tela real). Depois de cada "
        "passo que muda a tela, tire outro screenshot para conferir antes de seguir. A primeira ação pede "
        "aprovação; o usuário pode retomar o controle mexendo o mouse (aí pare e pergunte se deve continuar). "
        "Prefira atalhos de teclado quando forem mais seguros. Nunca digite senhas nem dados que o usuário não "
        "pediu; não clique nas janelas do Pajé.",
        {"action": {"type": "string", "enum": ["screenshot", "move", "click", "double_click", "right_click",
                                               "middle_click", "drag", "scroll", "type", "key", "wait"]},
         "x": {"type": "integer", "description": "X na imagem do último screenshot"},
         "y": {"type": "integer", "description": "Y na imagem do último screenshot"},
         "x2": {"type": "integer", "description": "drag: X do destino"},
         "y2": {"type": "integer", "description": "drag: Y do destino"},
         "text": {"type": "string", "description": "type: texto a digitar (\\n = Enter)"},
         "keys": {"type": "string", "description": "key: teclas separadas por espaço, ex.: 'ctrl+s', 'Return', "
                                                   "'alt+Tab', 'ctrl+l Return'"},
         "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
         "amount": {"type": "integer", "description": "scroll: quantos passos (1-15)"},
         "seconds": {"type": "number", "description": "wait: segundos (até 10)"}}, ["action"]),
    _fn("open_file", "Abre um arquivo ou pasta no programa padrão, na tela do usuário (PDF no leitor, foto no "
        "visualizador, texto no editor, pasta no gerenciador de arquivos). Não abre programas nem scripts. Depois "
        "use use_computer screenshot para ver o resultado.",
        {"path": {"type": "string", "description": "Ex.: ~/Documentos/relatorio.pdf, ~/Imagens"}}, ["path"]),
]
COMPUTER_TOOLS = {"use_computer", "open_file"}

# Ferramentas de ORQUESTRAÇÃO: só o Pajé tem (subagentes nunca criam agentes nem delegam)
ORCH_SCHEMAS = [
    _fn("listar_agentes", "Lista os subagentes do Pajé: nome, id, papel, ferramentas, modelo, estado e trabalhos."),
    _fn("criar_subagente", "Cria um subagente novo (com a própria alma .soul.md) quando nenhum dos existentes "
        "serve para o trabalho. Dê só as ferramentas necessárias. O usuário vê e pode editar ou apagar depois.",
        {"nome": {"type": "string", "description": "Nome do subagente, ex.: Pesquisador"},
         "papel": {"type": "string", "description": "Uma frase sobre o que ele faz"},
         "alma": {"type": "string", "description": "Markdown descrevendo comportamento e método de trabalho"},
         "ferramentas": {"type": "array", "items": {"type": "string"},
                         "description": "Subconjunto de: list_dir, read_file, system_info, write_file, run_command, "
                                        "take_screenshot, open_app, manage_service, set_kernel_param, st_run, "
                                        "st_stage_change, st_diff, use_computer, open_file"},
         "conexao": {"type": "string", "description": "Conexão de IA (padrão: principal)"},
         "modelo": {"type": "string", "description": "Modelo (vazio = padrão da conexão)"}},
        ["nome", "papel", "alma"]),
    _fn("delegar_tarefas", "Entrega trabalhos a subagentes. Todos rodam em PARALELO. Devolve os ids; depois use "
        "aguardar_tarefas para receber os resultados.",
        {"tarefas": {"type": "array", "items": {"type": "object", "properties": {
            "agente": {"type": "string", "description": "id ou nome do subagente"},
            "titulo": {"type": "string"}, "instrucoes": {"type": "string"}},
            "required": ["agente", "titulo", "instrucoes"]}}}, ["tarefas"]),
    _fn("aguardar_tarefas", "Espera os trabalhos terminarem e devolve os relatórios dos subagentes. Sem ids, espera "
        "todos os trabalhos delegados nesta conversa.",
        {"ids": {"type": "array", "items": {"type": "string"}},
         "segundos": {"type": "integer", "description": "Tempo máximo de espera (padrão 300, máximo 900)"}}),
    _fn("ver_tarefas", "Mostra o estado dos trabalhos (sem ids: os desta conversa).",
        {"ids": {"type": "array", "items": {"type": "string"}}}),
    _fn("cancelar_tarefa", "Cancela um trabalho que ainda não terminou.", {"id": {"type": "string"}}, ["id"]),
]
ORCH_TOOLS = {t["function"]["name"] for t in ORCH_SCHEMAS}

AGENT_PROMPT = (
    "Você é o Pajé, a IA do sistema operacional TUPIOS (NixOS). Responda em português do Brasil, "
    "de forma clara e objetiva, usando Markdown quando ajudar.\n"
    "Você pode AGIR no computador com as ferramentas disponíveis. Regras:\n"
    "- Toda ação passa pelo gerenciador de permissões do TUPIOS: algumas são liberadas, outras pedem aprovação "
    "do usuário e outras são negadas. Isso é decidido pelo sistema, não por você.\n"
    "- Se uma ação for NEGADA, não tente contornar (outro comando, outro caminho, sudo): explique ao usuário o "
    "motivo informado e, se fizer sentido, diga como ele pode liberar em 'Permissões da IA'.\n"
    "- Nunca tente alterar a política de permissões, o log de auditoria, o executor ou a chave da API.\n"
    "- Use a ferramenta mais específica e a ação mínima necessária. Antes de mudanças grandes, explique o plano.\n"
    "- Ao terminar, diga o que foi feito e o resultado.\n"
    "- Usar o computador (use_computer/open_file) é ASSISTIDO: o usuário vê tudo na tela. Prefira ferramentas "
    "diretas (read_file, write_file, run_command) quando bastarem; use mouse e teclado para programas gráficos "
    "ou quando o usuário pedir. Trabalhe em passos curtos com screenshot entre eles. Se o controle voltar para o "
    "usuário (ele mexeu no mouse), pare e pergunte antes de continuar. Nunca digite senhas.\n"
    "\n"
    "Arquitetura do TUPIOS (explique assim quando perguntarem):\n"
    "- PD (Production Domain): o sistema real do usuário, onde ele trabalha.\n"
    "- ST (Staging): ambiente de ensaio isolado — um container com a MESMA configuração do PD, sem a pasta "
    "pessoal real e sem rede — onde mudanças de sistema são testadas antes de chegar ao PD.\n"
    "- SEC (Safety Core): sistema de resgate independente, que volta gerações/snapshots anteriores sem tocar "
    "no /home.\n"
    "Regras para mudanças de sistema (pacotes, serviços, opções NixOS, parâmetros do kernel permanentes):\n"
    "1. Veja com system_info se o ST está disponível. Se estiver: ensaie com st_stage_change, mostre o "
    "resultado com st_diff e só então use st_promote (que sempre pede aprovação ao usuário). Se o usuário "
    "desistir, st_discard.\n"
    "2. Se o ST NÃO estiver disponível (ex.: ISO live), diga isso claramente, com o motivo informado, e ofereça "
    "a alternativa: set_kernel_param para parâmetros do kernel (temporário, some ao reiniciar, com Desfazer).\n"
    "3. O ST compartilha o KERNEL do PD. Parâmetros do kernel (sysctl globais como vm.*, a maioria de kernel.*, e "
    "parâmetros de boot) NÃO podem ser ensaiados no container: diga isso ao usuário e NUNCA afirme que testou. "
    "Alternativas: set_kernel_param (efeito real agora, temporário, com Desfazer) e, para tornar permanente, "
    "st_stage_change com sysctl + st_promote (o ST valida só que a configuração constrói).\n"
    "4. Só diga que algo foi ensaiado ou aplicado se a ferramenta confirmou; repita as verificações que ela "
    "devolveu (inclusive falhas)."
)


def request(path, payload, timeout=None):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    if timeout:
        s.settimeout(timeout)
    try:
        s.connect(path)
        s.sendall(json.dumps(payload).encode("utf-8"))
        s.shutdown(socket.SHUT_WR)
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return json.loads(buf.decode("utf-8") or "{}")
    finally:
        s.close()


def exec_op(op, timeout=None, **payload):
    try:
        return request(EXEC_SOCKET, dict(payload, op=op), timeout)
    except (OSError, ValueError) as e:
        return {"status": "error", "error": f"Executor da IA indisponível ({e}). "
                                            "Verifique: systemctl status tupi-agent-exec"}


def paje_socket():
    run = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return os.environ.get("TUPI_PAJE_SOCKET") or os.path.join(run, "tupi-paje", "paje.sock")


def paje_op(method, timeout=30, **params):
    """API local do motor do Pajé (serviço de usuário tupi-paje)."""
    try:
        return request(paje_socket(), {"method": method, "params": params}, timeout)
    except (OSError, ValueError) as e:
        return {"error": f"Motor do Pajé indisponível ({e}). Verifique: systemctl --user status tupi-paje"}


def perm_op(kind, **payload):
    try:
        return request(PERM_SOCKET, dict(payload, type=kind), 20)
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"Permission Manager indisponível ({e})."}


def result_for_model(r):
    """Resultado do executor -> texto para o modelo (+ se é erro)."""
    status = r.get("status")
    if status == "denied":
        return f"AÇÃO NÃO EXECUTADA. Motivo: {r.get('reason') or r.get('error')}", True
    if status not in ("ok", "error"):
        return f"Falha: {r.get('error') or r.get('reason') or 'desconhecida'}", True
    res = r.get("result")
    text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False)
    if len(text) > 20000:
        text = text[:20000] + f"\n[saída cortada: {len(text)} caracteres no total]"
    where = {"ST": "[executado no ST — ensaio, não mudou o PD] ", "PD": ""}.get(r.get("onde"), "")
    head = where + f"código de saída {r.get('exit_code')}" + (" (ERRO)" if status == "error" else "")
    return f"{head}\n{text}", status == "error"
