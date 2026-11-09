# Pacote Python compartilhado do agente (política, Permission Manager, executor, CLI, modo proativo).
# Usado por permission-manager.nix, tupi-agent.nix e tupi-assistant.nix.
{ pkgs }:
pkgs.stdenvNoCC.mkDerivation {
  pname = "tupi-agent";
  version = "0.1.0";
  src = ../../apps/tupi-agent;
  dontConfigure = true;
  dontBuild = true;
  installPhase = ''
    runHook preInstall
    mkdir -p $out/lib/python
    cp -r tupi_agent $out/lib/python/
    mkdir -p $out/share/doc/tupi-agent
    cp HARNESS.md $out/share/doc/tupi-agent/
    mkdir -p $out/share/doc/tupi-agent
    cp HARNESS.md $out/share/doc/tupi-agent/
    runHook postInstall
  '';
}
