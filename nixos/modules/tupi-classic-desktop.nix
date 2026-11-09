# TUPIOS — Classic Desktop Module
# Área de trabalho de janelas no estilo Windows, sobre XFCE (X11):
#   - uma barra embaixo: [Iniciar] [atalhos fixos] [janelas abertas] ... [bandeja] [relógio] [mostrar área de trabalho]
#   - janelas com minimizar / maximizar / fechar, Alt+Tab, área de trabalho com ícones
#   - gerenciador de arquivos Thunar (voltar/avançar), Firefox com favoritos das chaves de API
# Leve e robusto em VMs (não depende de aceleração 3D).
{ config, pkgs, lib, ... }:

let
  cfg = config.tupi.classicDesktop;

  # Layout padrão do painel (formato xfconf). Instalado como padrão do sistema em
  # /etc/xdg/xfce4/xfconf/...: o painel já nasce assim, sem a pergunta de 1º uso.
  panelXml = pkgs.writeText "xfce4-panel.xml" ''
    <?xml version="1.0" encoding="UTF-8"?>
    <channel name="xfce4-panel" version="1.0">
      <property name="configver" type="int" value="2"/>
      <property name="panels" type="array">
        <value type="int" value="1"/>
        <property name="dark-mode" type="bool" value="true"/>
        <property name="panel-1" type="empty">
          <property name="position" type="string" value="p=10;x=0;y=0"/>
          <property name="length" type="uint" value="100"/>
          <property name="position-locked" type="bool" value="true"/>
          <property name="size" type="uint" value="40"/>
          <property name="icon-size" type="uint" value="24"/>
          <property name="plugin-ids" type="array">
            <value type="int" value="1"/>
            <value type="int" value="2"/>
            <value type="int" value="3"/>
            <value type="int" value="4"/>
            <value type="int" value="5"/>
            <value type="int" value="6"/>
            <value type="int" value="7"/>
            <value type="int" value="8"/>
            <value type="int" value="9"/>
            <value type="int" value="10"/>
            <value type="int" value="11"/>
            <value type="int" value="12"/>
            <value type="int" value="13"/>
            <value type="int" value="14"/>
          </property>
        </property>
      </property>
      <property name="plugins" type="empty">
        <property name="plugin-1" type="string" value="whiskermenu">
          <property name="button-title" type="string" value="Iniciar"/>
          <property name="show-button-title" type="bool" value="true"/>
          <property name="show-button-icon" type="bool" value="true"/>
          <property name="button-icon" type="string" value="tupios-ia"/>
          <property name="favorites" type="array">
            <value type="string" value="firefox.desktop"/>
            <value type="string" value="thunar.desktop"/>
            <value type="string" value="xfce4-terminal.desktop"/>
            <value type="string" value="tupi-assistant.desktop"/>
            <value type="string" value="tupi-ai-config.desktop"/>
          </property>
        </property>
        <property name="plugin-2" type="string" value="separator">
          <property name="style" type="uint" value="0"/>
        </property>
        <property name="plugin-3" type="string" value="launcher">
          <property name="items" type="array">
            <value type="string" value="firefox.desktop"/>
          </property>
        </property>
        <property name="plugin-4" type="string" value="launcher">
          <property name="items" type="array">
            <value type="string" value="thunar.desktop"/>
          </property>
        </property>
        <property name="plugin-5" type="string" value="launcher">
          <property name="items" type="array">
            <value type="string" value="xfce4-terminal.desktop"/>
          </property>
        </property>
        <property name="plugin-6" type="string" value="launcher">
          <property name="items" type="array">
            <value type="string" value="tupi-assistant.desktop"/>
          </property>
        </property>
        <property name="plugin-7" type="string" value="separator">
          <property name="style" type="uint" value="0"/>
        </property>
        <property name="plugin-8" type="string" value="tasklist">
          <property name="grouping" type="uint" value="1"/>
          <property name="show-labels" type="bool" value="true"/>
        </property>
        <property name="plugin-9" type="string" value="separator">
          <property name="expand" type="bool" value="true"/>
          <property name="style" type="uint" value="0"/>
        </property>
        <property name="plugin-10" type="string" value="systray">
          <property name="square-icons" type="bool" value="true"/>
        </property>
        <property name="plugin-11" type="string" value="separator">
          <property name="style" type="uint" value="0"/>
        </property>
        <property name="plugin-12" type="string" value="clock"/>
        <property name="plugin-13" type="string" value="separator">
          <property name="style" type="uint" value="0"/>
        </property>
        <property name="plugin-14" type="string" value="showdesktop"/>
      </property>
    </channel>
  '';
in
{
  options.tupi.classicDesktop = {
    enable = lib.mkEnableOption "área de trabalho TUPIOS estilo Windows (XFCE, barra embaixo)";

    autoLoginUser = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "tupi";
      description = "Usuário que entra direto na área de trabalho, sem tela de login (null = pede login).";
    };
  };

  config = lib.mkIf cfg.enable {
    services.xserver.enable = true;
    services.xserver.desktopManager.xfce.enable = true;
    services.xserver.displayManager.lightdm.enable = true;
    services.displayManager.defaultSession = "xfce";
    services.displayManager.autoLogin = lib.mkIf (cfg.autoLoginUser != null) {
      enable = true;
      user = cfg.autoLoginUser;
    };

    # Teclado brasileiro (ABNT2) na interface gráfica
    services.xserver.xkb.layout = lib.mkDefault "br";

    # Áudio (alto-falante e microfone — gravação de voz para a IA)
    services.pipewire = {
      enable = true;
      alsa.enable = true;
      pulse.enable = true;
    };
    security.rtkit.enable = true;

    # Fontes padrão (inclui emoji colorido, usado nas respostas da IA)
    fonts.enableDefaultPackages = true;

    # Perfis "minimal" desligam isso; a interface gráfica precisa
    fonts.fontconfig.enable = true;
    xdg.icons.enable = true;
    xdg.mime.enable = true;
    xdg.autostart.enable = true;

    environment.systemPackages = with pkgs; [
      xfce.xfce4-whiskermenu-plugin # menu Iniciar com busca
      xfce.mousepad # bloco de notas
      xfce.ristretto # visualizador de imagens
      xfce.xfce4-taskmanager # gerenciador de tarefas
      xfce.xfce4-screenshooter # captura de tela (tecla Print)
      file-roller # abrir/criar .zip
    ];

    environment.etc."xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-panel.xml".source = panelXml;
    environment.etc."xdg/xfce4/panel/default.xml".source = panelXml;

    # Programas padrão (sem isso o XFCE pergunta qual navegador/terminal usar)
    environment.etc."xdg/xfce4/helpers.rc".text = ''
      WebBrowser=firefox
      FileManager=thunar
      TerminalEmulator=xfce4-terminal
    '';

    programs.firefox = {
      enable = true;
      policies = {
        DisableTelemetry = true;
        DisplayBookmarksToolbar = "always";
        OverrideFirstRunPage = "";
        OverridePostUpdatePage = "";
        ManagedBookmarks = [
          { toplevel_name = "Chaves de API (IA)"; }
          { name = "OpenAI — chaves"; url = "https://platform.openai.com/api-keys"; }
          { name = "Anthropic (Claude) — Console"; url = "https://platform.claude.com/"; }
          { name = "Groq — chaves (grátis)"; url = "https://console.groq.com/keys"; }
          { name = "OpenRouter — chaves"; url = "https://openrouter.ai/keys"; }
          { name = "DeepSeek — chaves"; url = "https://platform.deepseek.com/api_keys"; }
          { name = "Google Gemini — chaves"; url = "https://aistudio.google.com/apikey"; }
        ];
      };
    };
  };
}
