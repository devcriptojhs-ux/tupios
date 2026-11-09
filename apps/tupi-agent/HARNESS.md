# Harness do Pajé

O **Pajé** é a IA do TUPIOS. Ele conversa com você no app (janela, chat rápido e bolinha) e coordena uma
equipe de **subagentes**: divide trabalhos, roda vários em paralelo e junta os relatórios.

```
Você ──► Pajé ──► subagentes (em paralelo) ──► executor + Permission Manager ──► sistema
         constituição fixa     constituição fixa         aprovação, sandbox,
         + alma do Pajé        + alma própria + modelo   auditoria
```

## Regras que nenhuma alma muda

- Toda ação de sistema (do Pajé ou de um subagente) passa pelo executor `tupi-agent-exec` e pelo
  Permission Manager, com o modo atual (restrito / perguntar / livre). O pedido de aprovação mostra quem
  pediu: "**Pesquisador** (subagente do Pajé) pede sua permissão".
- A alma descreve personalidade e método. Ela **não dá permissão** a nada.
- Subagentes **não** criam agentes, **não** delegam e **nunca** promovem mudanças para o PD
  (`st_promote` só existe para o Pajé, e sempre pede a sua aprovação).
- Ferramenta que não está na alma do subagente é recusada antes de chegar ao executor.
- **PARAR IA** cancela todos os trabalhos (em poucos segundos; pedidos de aprovação pendentes são
  negados na hora), e nenhum trabalho novo começa até você retomar.
- Cada subagente tem um orçamento diário de tokens. Tudo fica na auditoria (`agente_criado`,
  `trabalho_criado`, `trabalho_concluido`, `trabalhos_cancelados`, `alma_do_paje_editada` e o nome do
  agente em cada ação).

## Arquivos (`~/.local/share/tupios-ia/paje/`)

| Arquivo | O que é |
|---|---|
| `paje.soul.md` | alma do Pajé (editável). O cabeçalho pode trocar a conexão e o modelo dele |
| `agentes/<id>.soul.md` | uma alma por subagente |
| `trabalhos.json` | quadro de trabalhos (os últimos 300) |
| `estado.json` | uso de tokens do dia por agente |

Formato da alma de um subagente (cabeçalho + Markdown livre):

```markdown
---
nome: Pesquisador
papel: Procura informações nos seus arquivos e entrega um resumo
conexao: principal          # conexão de IA (ver abaixo)
modelo:                     # vazio = modelo padrão da conexão
ferramentas: list_dir, read_file, system_info
orcamento_tokens_dia: 100000
rotina_minutos: 0           # > 0: roda "rotina" sozinho a cada N minutos
rotina:
criado_por: usuario         # ou "paje", quando o Pajé criou
criado_em: 2026-11-07T10:00
---

# Quem eu sou
Sou o Pesquisador...

# Como trabalho
- ...
```

Ferramentas que um subagente pode receber: `list_dir`, `read_file`, `system_info`, `write_file`,
`run_command`, `take_screenshot`, `open_app`, `manage_service`, `set_kernel_param`, `st_run`,
`st_stage_change`, `st_diff`, `use_computer`, `open_file`.

## Usar o computador (mouse, teclado e abrir arquivos) — assistido

`use_computer` vê a tela (`screenshot`) e age como uma pessoa: `click`, `double_click`, `right_click`,
`middle_click`, `move`, `drag`, `scroll`, `type`, `key` e `wait`. `open_file` abre um arquivo ou pasta no
programa padrão. O modelo recebe o print reduzido para caber em 1280x800, e as coordenadas que ele usa são
nessa imagem (o executor converte para a tela real). Só os 2 prints mais recentes vão como imagem.

- A primeira ação pede aprovação, em qualquer modo, e a categoria nunca vira "permitida". "Permitir nesta
  conversa" vale até você **mexer no mouse**, clicar em **Devolver o controle** (faixa no topo da tela) ou
  ficar 10 minutos sem uso (`tupi.agent.computerUse.idleMinutes`). PARAR IA também devolve o controle.
- Ao liberar, as janelas do Pajé saem da frente (fica a bolinha e a faixa) e voltam quando a IA para de usar
  a tela. Pelo terminal: `tupi-agent devolver`.
- A IA não clica nem digita nas janelas do Pajé (aprovações, permissões, bolinha, faixa), conferindo a janela
  que está de fato por cima no ponto, e não age enquanto houver um pedido de aprovação esperando você: assim
  ela nunca aprova a si mesma.
- Só um agente por vez usa a tela; o pedido e a faixa mostram quem é.
- Texto digitado é julgado como um comando: `sudo …`, `rm -rf …`, `tupi-agent approve …` e afins seguem as
  mesmas regras do `run_command` (negados ou com confirmação extra).
- Trocar para um terminal de texto (Ctrl+Alt+F1…F12), derrubar a interface ou Ctrl+Alt+Del: bloqueado.
- `open_file` não abre programas nem scripts.

Você pode editar as almas no app (Agentes → Editar), em qualquer editor de texto ou pelo terminal.
Editar o arquivo à mão vale na próxima vez que o agente trabalhar.

## Conexões de IA (um modelo por agente)

A conexão `principal` é a do "Configurar IA". Outras conexões ficam em `conexoes` dentro de
`/etc/tupi/ai-config.json` (arquivo protegido: a IA e os subagentes não conseguem ler as chaves). Cada
subagente escolhe a conexão e o modelo na alma. Adicione conexões no app: Harness → Adicionar conexão
(OpenAI, Anthropic oficial, compatível com Anthropic, Groq, OpenRouter, DeepSeek, Gemini, Ollama ou
qualquer API compatível com OpenAI).

**Provedor próprio de um subagente:** no formulário do subagente, escolha "Provedor próprio", informe o
provedor, a URL base e a chave e clique em **Buscar modelos**. O sistema cria a conexão `ag-<id>`, só dele, e
na alma fica apenas `conexao: ag-<id>`. Ao editar, deixe a chave em branco para manter a salva. Se você
trocar a URL, precisa digitar a chave de novo (a chave antiga nunca vai para outro endereço). Apagar o
subagente apaga a conexão dele.

## Terminal: `paje`

```
paje                                   estado do motor
paje agentes                           lista os subagentes
paje agente ID                         mostra a alma
paje novo NOME --papel "…" [--base pesquisador|analista|escritor|operador] [--ferramentas a,b]
               [--conexao NOME] [--modelo ID] [--alma ARQUIVO.md]
               [--provedor custom|openai|anthropic|… --url URL --chave CHAVE]   (provedor próprio)
paje provedor ID --provedor P [--url URL] [--chave CHAVE] [--modelo M]  |  paje provedor ID --remover
paje modelos ID                        modelos do provedor do subagente
paje modelos --provedor P --url URL    (antes de salvar; a chave é pedida sem aparecer na tela)
paje apagar ID
paje trabalho AGENTE "instruções" [--titulo "…"]
paje trabalhos                         quadro de trabalhos
paje ver ID                            passos e relatório de um trabalho
paje esperar ID [ID…] [--segundos N]
paje cancelar ID
paje harness                           constituição, ferramentas, conexões, limites e arquivos
```

## API local

O motor roda no serviço de usuário `tupi-paje` (`systemctl --user status tupi-paje`) e atende só o seu
usuário, num socket Unix: `$XDG_RUNTIME_DIR/tupi-paje/paje.sock`. Cada conexão leva um pedido JSON
`{"method": "...", "params": {...}}`. Feche a escrita depois de enviar e leia a resposta JSON.

```sh
printf '{"method":"agents/list","params":{}}' | socat - UNIX-CONNECT:$XDG_RUNTIME_DIR/tupi-paje/paje.sock
```

| Método | Parâmetros |
|---|---|
| `status` | — |
| `harness` | — |
| `agents/list` · `agents/templates` | — |
| `agents/get` · `agents/delete` | `id` |
| `agents/create` | `nome`, `papel`, `alma`, `ferramentas`, `conexao`, `modelo`, `orcamento_tokens_dia`, `rotina_minutos`, `rotina`, `modelo_base` |
| `agents/update` | `id` + campos acima, ou `texto` (a alma inteira, com cabeçalho) |
| `tasks/create` | `agente`, `titulo`, `instrucoes` |
| `tasks/create_many` | `tarefas`: lista de `{agente, titulo, instrucoes}` (rodam em paralelo) |
| `tasks/list` | `ids` ou `conversa`, `limite` |
| `tasks/get` · `tasks/cancel` | `id` |
| `tasks/wait` | `ids` ou `conversa`, `segundos` |
| `tasks/cancel_all` | `motivo` |
| `paje/soul` | `texto` (sem `texto`, só lê) |

## Limites (configuráveis em `tupi.agent.paje.*` no NixOS)

| Opção | Padrão |
|---|---|
| `maxParallel` | 3 trabalhos ao mesmo tempo |
| `maxSubagents` | 12 subagentes |
| `maxSteps` | 12 passos por trabalho |
| `dailyTokenBudget` | 100000 tokens por dia, por subagente |
