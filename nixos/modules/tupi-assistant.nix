# TUPIOS — Assistente de IA (app estilo ChatGPT + bolinha flutuante)
#   - janela "Pajé": conversas, fotos, PDFs, áudio (transcrição local), prints
#   - bolinha sempre visível e arrastável: clique = chat rápido; botão direito = menu
#     (print da tela inteira / recorte para perguntar à IA)
# Usa o tupi-ai-router (tupi.aiRouter) para falar com o provedor de IA.
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.assistant;

  # Modelo de transcrição do whisper.cpp (multilíngue "base", ~148 MB): roda
  # localmente, então o áudio do usuário não sai da máquina.
  whisperModel = pkgs.fetchurl {
    name = "ggml-base.bin";
    url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin";
    hash = "sha256-YO1bw90U7qhWST0zQ0m0BXgt3K8AKNS130CINF+6Lv4=";
  };

  python = pkgs.python3.withPackages (ps: [ ps.pygobject3 ps.pycairo ]);

  # Ferramentas do agente (esquemas, cliente do executor): mesmo pacote do tupi-agent-exec
  agentPkg = import ./tupi-agent-package.nix { inherit pkgs; };

  # O whisper-cpp do nixpkgs 25.05 é compilado com GGML_BACKEND_DL + CPU_ALL_VARIANTS,
  # mas as bibliotecas libggml-cpu-*.so não são instaladas: o whisper-cli dá
  # "Segmentation fault" ao carregar o modelo. Aqui o backend de CPU vai embutido,
  # em duas variantes: AVX2 (rápida, CPUs de ~2013+) e básica (qualquer x86-64).
  # O app escolhe pela /proc/cpuinfo.
  whisperFor = suffix: extraFlags: pkgs.whisper-cpp.overrideAttrs (old: {
    pname = "whisper-cpp-tupi-${suffix}";
    cmakeFlags =
      (lib.filter (f: !(lib.hasPrefix "-DGGML_BACKEND_DL" f) && !(lib.hasPrefix "-DGGML_CPU_ALL_VARIANTS" f))
        (old.cmakeFlags or [ ]))
      ++ [ "-DGGML_BACKEND_DL:BOOL=FALSE" "-DGGML_CPU_ALL_VARIANTS:BOOL=FALSE" ]
      ++ extraFlags;
  });
  whisperBasic = whisperFor "basic" [ ];
  whisperAvx2 = whisperFor "avx2" [
    "-DGGML_SSE42:BOOL=TRUE"
    "-DGGML_AVX:BOOL=TRUE"
    "-DGGML_AVX2:BOOL=TRUE"
    "-DGGML_FMA:BOOL=TRUE"
    "-DGGML_F16C:BOOL=TRUE"
  ];

  runtimeTools = [
    pkgs.ffmpeg-headless # converte qualquer áudio para WAV 16 kHz
    pkgs.poppler-utils # pdftotext / pdfinfo
    pkgs.pipewire # pw-record (microfone)
    pkgs.alsa-utils # arecord (alternativa sem PipeWire)
    pkgs.xfce.xfce4-screenshooter # recorte de parte da tela
  ];

  app = pkgs.stdenv.mkDerivation {
    pname = "tupi-assistant";
    version = "0.1.0";
    src = ../../apps/tupi-assistant;

    nativeBuildInputs = [ pkgs.wrapGAppsHook3 pkgs.gobject-introspection pkgs.makeWrapper ];
    buildInputs = [
      pkgs.gtk3
      pkgs.webkitgtk_4_1
      pkgs.glib-networking
      pkgs.librsvg
      pkgs.gdk-pixbuf
    ];

    dontWrapGApps = true;
    dontConfigure = true;
    dontBuild = true;

    installPhase = ''
      runHook preInstall
      mkdir -p $out/share/tupi-assistant $out/bin
      cp -r app.py ui $out/share/tupi-assistant/
      runHook postInstall
    '';

    # O wrapper leva GI_TYPELIB_PATH, XDG_DATA_DIRS etc. (wrapGAppsHook3) e as ferramentas
    postFixup = ''
      makeWrapper ${python}/bin/python3 $out/bin/tupi-assistant \
        "''${gappsWrapperArgs[@]}" \
        --add-flags $out/share/tupi-assistant/app.py \
        --prefix PATH : ${lib.makeBinPath runtimeTools} \
        --prefix PYTHONPATH : ${agentPkg}/lib/python \
        --set-default TUPI_AI_CONFIG ${config.tupi.aiRouter.configFile} \
        --set-default TUPI_WHISPER_MODEL ${whisperModel} \
        --set-default TUPI_WHISPER_CLI_BASIC ${whisperBasic}/bin/whisper-cli \
        --set-default TUPI_WHISPER_CLI_AVX2 ${whisperAvx2}/bin/whisper-cli \
        --set-default TUPI_AI_URL http://127.0.0.1:${toString config.tupi.aiRouter.port}
    '';
  };

  desktopItem = pkgs.makeDesktopItem {
    name = "tupi-assistant";
    desktopName = "Pajé";
    genericName = "Assistente de IA";
    comment = "Converse com o Pajé, a IA do TUPIOS: texto, fotos, PDFs, áudio e prints; coordena subagentes";
    exec = "${app}/bin/tupi-assistant --full";
    icon = "tupios-ia";
    categories = [ "Utility" ];
    startupNotify = false;
  };

  # Menu Iniciar → "Permissões da IA" (modo restrito/perguntar/livre, categorias, memória)
  permsItem = pkgs.makeDesktopItem {
    name = "tupi-ai-permissions";
    desktopName = "Pajé — permissões e agentes";
    genericName = "Permissões, agentes e harness do Pajé";
    comment = "O que o Pajé e os subagentes podem fazer, equipe, trabalhos e harness";
    exec = "${app}/bin/tupi-assistant --settings";
    icon = "security-high";
    categories = [ "Settings" ];
    startupNotify = false;
  };
in
{
  options.tupi.assistant = {
    enable = lib.mkEnableOption "Pajé, a IA do TUPIOS (app estilo ChatGPT + bolinha flutuante)";

    bubbleAtLogin = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Mostrar a bolinha flutuante da IA ao entrar na área de trabalho.";
    };

    package = lib.mkOption {
      type = lib.types.package;
      default = app;
      readOnly = true;
      description = "Pacote do assistente (para testes).";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = config.tupi.aiRouter.enable;
        message = "tupi.assistant precisa de tupi.aiRouter.enable = true";
      }
    ];

    environment.systemPackages = [ app desktopItem ] ++ lib.optional (config.tupi.agent.enable or false) permsItem;

    environment.etc."xdg/autostart/tupi-assistant.desktop" = lib.mkIf cfg.bubbleAtLogin {
      text = ''
        [Desktop Entry]
        Type=Application
        Name=Pajé (bolinha)
        Exec=${app}/bin/tupi-assistant --bubble
        Icon=tupios-ia
        NoDisplay=true
        X-GNOME-Autostart-enabled=true
      '';
    };
  };
}
