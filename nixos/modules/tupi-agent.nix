# TUPIOS — IA como AGENTE (age no sistema), restrita por padrão.
#
# Peças:
#   - tupi-agent-exec (serviço): executa cada ferramenta numa unidade systemd transitória isolada
#     (slice tupi-agent.slice), depois de o Permission Manager classificar/decidir/auditar.
#   - Política: tupi.agent.mode + tupi.agent.policyOverrides (NixOS) -> /etc/tupi/agent-policy.json
#     (runtime, tela "Permissões da IA") — calculada num só lugar (tupi_agent/policy.py).
#   - tupi-agent (CLI): status, PARAR (stop), aprovar/negar, desfazer, política.
#   - tupi-agent-loop (serviço de usuário): modo proativo.
#
# Privilégios: não há sudo genérico para a IA. O executor (root) só faz operações privilegiadas por
# caminhos específicos (controlar serviços, aplicar a configuração NixOS) e só depois de a política
# permitir; comandos rodam como o próprio usuário ou como "tupi-agent".
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.agent;
  agentPkg = import ./tupi-agent-package.nix { inherit pkgs; };
  py = "${pkgs.python3}/bin/python3";
  pythonPath = "${agentPkg}/lib/python";

  categories = [
    "read" "read_secret" "write_user_data" "write_system_config" "install_package"
    "stage_change" "promote_change" "tune_kernel" "network_egress"
    "execute_binary" "modify_bootloader" "access_hardware" "privilege_escalation" "delete_system_file"
    "control_desktop"
  ];

  # Usuários que usam a IA (grupo tupi-ia): o agente "tupi-agent" ganha acesso de LEITURA à pasta
  # pessoal deles (ACL só na pasta raiz; arquivos privados 0600 e ~/.ssh continuam inacessíveis).
  agentUsers = lib.filter (u: u.isNormalUser && lib.elem "tupi-ia" u.extraGroups) (lib.attrValues config.users.users);

  cli = pkgs.writeShellScriptBin "tupi-agent" ''
    export PYTHONPATH=${pythonPath}''${PYTHONPATH:+:$PYTHONPATH}
    exec ${py} -m tupi_agent.cli "$@"
  '';

  # O harness do Pajé pelo terminal (subagentes, trabalhos, almas)
  pajeCli = pkgs.writeShellScriptBin "paje" ''
    export PYTHONPATH=${pythonPath}''${PYTHONPATH:+:$PYTHONPATH}
    export TUPI_AI_CONFIG=''${TUPI_AI_CONFIG:-${config.tupi.aiRouter.configFile}}
    export TUPI_AI_URL=''${TUPI_AI_URL:-http://127.0.0.1:${toString config.tupi.aiRouter.port}}
    exec ${py} -m tupi_agent.paje_cli "$@"
  '';
in
{
  imports = [ ./permission-manager.nix ./ai-router.nix ./agent-orchestrator.nix ./tupi-st.nix ];

  options.tupi.agent = {
    enable = lib.mkEnableOption "IA como agente (ferramentas restritas pelo Permission Manager)";

    mode = lib.mkOption {
      type = lib.types.enum [ "restrito" "perguntar" "livre" ];
      default = "restrito";
      description = ''
        restrito: só leitura liberada; escrita no /home, rede e execução perguntam; o resto é negado.
        perguntar: tudo pergunta, exceto as categorias críticas (negadas).
        livre: tudo liberado, inclusive as críticas (o modo livre pela tela exige confirmação dupla e prazo).
        Em qualquer modo: a IA nunca altera a própria política nem a auditoria; destrutivos sempre confirmam.
      '';
    };

    policyOverrides = lib.mkOption {
      type = lib.types.attrsOf (lib.types.enum [ "allow" "ask" "deny" ]);
      default = { };
      example = { install_package = "ask"; tune_kernel = "ask"; network_egress = "allow"; };
      description = ''
        Ajuste por categoria, aplicado sobre o modo (o arquivo de runtime ainda pode sobrepor).
        Categorias: ${lib.concatStringsSep ", " categories}.
        promote_change (aplicar no PD uma mudança ensaiada no ST) só aceita "ask" ou "deny": sempre pergunta.
      '';
    };

    liveSystem = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Sistema live (ISO): não há ST nem configuração no disco; a IA explica e oferece mudanças temporárias.";
    };

    approvalTimeout = lib.mkOption { type = lib.types.ints.positive; default = 120; description = "Segundos até um pedido de aprovação virar negação."; };
    commandTimeout = lib.mkOption { type = lib.types.ints.positive; default = 60; description = "Tempo padrão por comando (máx. 600 s)."; };
    maxOutputBytes = lib.mkOption { type = lib.types.ints.positive; default = 65536; description = "Saída máxima guardada por comando."; };
    actionsPerMinute = lib.mkOption { type = lib.types.ints.positive; default = 20; description = "Limite de ações da IA por minuto (por usuário)."; };

    flakeRef = lib.mkOption {
      type = lib.types.str;
      default = "/etc/nixos#tupi-pd";
      description = "Flake e configuração do PD (o ST usa nixosConfigurations.tupi-st do mesmo flake).";
    };
    agentConfigDir = lib.mkOption {
      type = lib.types.str;
      default = "nixos/pd";
      description = "Pasta (relativa ao flake) com agent-config.json e agent-extra.nix, mantidos pela IA.";
    };

    computerUse.idleMinutes = lib.mkOption {
      type = lib.types.ints.between 1 120;
      default = 10;
      description = "\"Permitir nesta conversa\" o uso do mouse/teclado expira depois de tantos minutos sem uso (mexer no mouse também devolve o controle na hora).";
    };

    paje = {
      enable = lib.mkOption { type = lib.types.bool; default = true; description = "Motor do Pajé: subagentes e trabalhos em paralelo (serviço de usuário tupi-paje)."; };
      maxParallel = lib.mkOption { type = lib.types.ints.between 1 16; default = 3; description = "Quantos trabalhos de subagentes rodam ao mesmo tempo."; };
      maxSubagents = lib.mkOption { type = lib.types.ints.between 1 64; default = 12; description = "Máximo de subagentes por usuário (inclui os criados pelo Pajé)."; };
      dailyTokenBudget = lib.mkOption { type = lib.types.ints.positive; default = 100000; description = "Orçamento diário padrão de tokens de cada subagente novo (cada alma pode mudar)."; };
      maxSteps = lib.mkOption { type = lib.types.ints.between 1 50; default = 12; description = "Passos (chamadas à IA) por trabalho antes de parar."; };
    };

    loop = {
      enable = lib.mkOption { type = lib.types.bool; default = true; description = "Modo proativo ligado por padrão (o usuário pode desligar no app)."; };
      intervalMinutes = lib.mkOption { type = lib.types.ints.positive; default = 15; description = "Intervalo dos ciclos (metade quando o usuário está ativo)."; };
      dailyTokenBudget = lib.mkOption { type = lib.types.ints.positive; default = 20000; description = "Limite de tokens por dia gastos pelo modo proativo."; };
      observeBrowser = lib.mkOption { type = lib.types.bool; default = false; description = "Observar domínios visitados no Firefox (desligado por privacidade)."; };
      silent = lib.mkOption { type = lib.types.bool; default = false; description = "Aprende mas não interrompe (sem notificações)."; };
    };

    package = lib.mkOption { type = lib.types.package; default = agentPkg; readOnly = true; description = "Pacote do agente."; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = config.tupi.permissionManager.enable; message = "tupi.agent precisa de tupi.permissionManager.enable = true"; }
      {
        assertion = lib.all (c: lib.elem c categories) (lib.attrNames cfg.policyOverrides);
        message = "tupi.agent.policyOverrides: categoria inválida. Válidas: ${lib.concatStringsSep ", " categories}";
      }
      {
        assertion = (cfg.policyOverrides.promote_change or "ask") != "allow";
        message = "tupi.agent.policyOverrides.promote_change: aplicar no PD sempre pergunta (use \"ask\" ou \"deny\").";
      }
    ];

    users.groups.tupi-ia = { };
    users.groups.tupi-agent = { };
    users.users.tupi-agent = {
      isSystemUser = true;
      group = "tupi-agent";
      description = "Pajé (ferramentas sem privilégio)";
      home = "/var/empty";
    };

    # Camada NixOS da política (o Permission Manager combina com o arquivo de runtime)
    environment.etc."tupi/agent-base-policy.json".text = builtins.toJSON {
      inherit (cfg) mode;
      overrides = cfg.policyOverrides;
    };

    # Camada runtime: editável pelos usuários do grupo tupi-ia (tela "Permissões da IA"/CLI);
    # as unidades da IA a veem só para leitura.
    systemd.tmpfiles.rules = [ "f /etc/tupi/agent-policy.json 0660 root tupi-ia -" ];

    system.activationScripts.tupi-agent-home-acl = lib.stringAfter [ "users" "groups" ] (lib.concatMapStrings (u: ''
      if [ -d "${u.home}" ]; then
        ${pkgs.acl}/bin/setfacl -m u:tupi-agent:rx "${u.home}" 2>/dev/null || true
      fi
    '') agentUsers);

    # Todas as ferramentas da IA rodam aqui: limite total e o PARAR IA para tudo de uma vez
    systemd.slices.tupi-agent = {
      description = "Pajé — ferramentas do agente e dos subagentes";
      sliceConfig = { MemoryMax = "3G"; TasksMax = 2048; };
    };

    systemd.services.tupi-agent-exec = {
      description = "Pajé — executor de ferramentas (isolado e auditado)";
      wantedBy = [ "multi-user.target" ];
      after = [ "tupi-perm-mgr.service" "systemd-tmpfiles-setup.service" ];
      wants = [ "tupi-perm-mgr.service" ];
      path = [ config.systemd.package pkgs.coreutils pkgs.util-linux pkgs.git pkgs.bash config.nix.package
               pkgs.diffutils ];
      environment = {
        PYTHONPATH = pythonPath;
        TUPI_AGENT_HELPER = "${agentPkg}/lib/python/tupi_agent/helper.py";
        TUPI_AGENT_PYTHON = py;
        TUPI_AGENT_APPROVAL_TIMEOUT = toString cfg.approvalTimeout;
        TUPI_AGENT_COMMAND_TIMEOUT = toString cfg.commandTimeout;
        TUPI_AGENT_MAX_OUTPUT = toString cfg.maxOutputBytes;
        TUPI_AGENT_ACTIONS_PER_MINUTE = toString cfg.actionsPerMinute;
        TUPI_AGENT_FLAKE = cfg.flakeRef;
        TUPI_AGENT_CONFIG_REL = cfg.agentConfigDir;
        TUPI_ST_ENABLED = if config.tupi.st.enable then "1" else "0";
        TUPI_ST_CONTAINER = config.tupi.st.containerName;
        TUPI_AGENT_LIVE = if cfg.liveSystem then "1" else "0";
        # Usar o computador (assistido): mouse/teclado, print redimensionado e abrir arquivos no app padrão
        TUPI_XDOTOOL = "${pkgs.xdotool}/bin/xdotool";
        TUPI_MAGICK = "${pkgs.imagemagick}/bin/magick";
        TUPI_XDG_OPEN = "${pkgs.xdg-utils}/bin/xdg-open";
        TUPI_XPROP = "${pkgs.xorg.xprop}/bin/xprop";
        TUPI_AGENT_CONTROL_IDLE = toString (cfg.computerUse.idleMinutes * 60);
      };
      serviceConfig = {
        Type = "simple";
        ExecStart = "${py} -u -m tupi_agent.execd";
        User = "root";
        Group = "tupi-ia";
        Restart = "on-failure";
        RestartSec = "2s";
        RuntimeDirectory = "tupi-agent";
        RuntimeDirectoryMode = "0750";
        StateDirectory = "tupi-agent";
        StateDirectoryMode = "0711";
        # O executor em si não escreve no sistema: as ferramentas rodam em unidades próprias
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        ReadWritePaths = [ "/var/lib/tupi-agent" "-/etc/nixos" ];
        PrivateTmp = true;
        NoNewPrivileges = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
      };
    };

    systemd.user.services.tupi-agent-loop = {
      description = "Pajé — modo proativo";
      wantedBy = [ "graphical-session.target" ];
      partOf = [ "graphical-session.target" ];
      after = [ "graphical-session.target" ];
      unitConfig.ConditionGroup = "tupi-ia";
      path = [ pkgs.xdotool pkgs.xprintidle pkgs.libnotify pkgs.coreutils config.systemd.package ];
      environment = {
        PYTHONPATH = pythonPath;
        TUPI_LOOP_ENABLED = if cfg.loop.enable then "1" else "0";
        TUPI_LOOP_INTERVAL = toString cfg.loop.intervalMinutes;
        TUPI_LOOP_DAILY_TOKENS = toString cfg.loop.dailyTokenBudget;
        TUPI_LOOP_BROWSER = if cfg.loop.observeBrowser then "1" else "0";
        TUPI_LOOP_SILENT = if cfg.loop.silent then "1" else "0";
        TUPI_AI_URL = "http://127.0.0.1:${toString config.tupi.aiRouter.port}";
      };
      serviceConfig = {
        ExecStart = "${py} -u -m tupi_agent.loop";
        Restart = "always";
        RestartSec = "30s";
        Nice = 10;
      };
    };

    # Pajé: motor dos subagentes, por usuário e SEM privilégio (as ações de sistema dos subagentes vão ao
    # tupi-agent-exec, como as do próprio Pajé). Almas e trabalhos em ~/.local/share/tupios-ia/paje.
    systemd.user.services.tupi-paje = lib.mkIf cfg.paje.enable {
      description = "Pajé — orquestração de subagentes (trabalhos em paralelo)";
      wantedBy = [ "default.target" ];
      unitConfig.ConditionGroup = "tupi-ia";
      environment = {
        PYTHONPATH = pythonPath;
        TUPI_AI_URL = "http://127.0.0.1:${toString config.tupi.aiRouter.port}";
        TUPI_PAJE_MAX_PARALLEL = toString cfg.paje.maxParallel;
        TUPI_PAJE_MAX_SUBAGENTS = toString cfg.paje.maxSubagents;
        TUPI_PAJE_BUDGET = toString cfg.paje.dailyTokenBudget;
        TUPI_PAJE_MAX_STEPS = toString cfg.paje.maxSteps;
      };
      serviceConfig = {
        ExecStart = "${py} -u -m tupi_agent.paje";
        RuntimeDirectory = "tupi-paje";          # %t/tupi-paje/paje.sock (só o usuário)
        RuntimeDirectoryMode = "0700";
        Restart = "on-failure";
        RestartSec = "5s";
        Nice = 5;
      };
    };

    environment.systemPackages = [ cli ] ++ lib.optional cfg.paje.enable pajeCli;
    # Documentação do harness (arquivos, formato das almas, conexões, CLI e API local)
    environment.etc."tupi/paje/HARNESS.md" = lib.mkIf cfg.paje.enable {
      source = "${agentPkg}/share/doc/tupi-agent/HARNESS.md";
    };
  };
}
