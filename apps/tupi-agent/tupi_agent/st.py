"""ST (Staging) do TUPIOS: ensaiar mudanças de configuração antes de aplicá-las no PD.

Arquitetura:
  PD  = o sistema real do usuário (Production Domain).
  ST  = container NixOS (containers.tupi-st, efêmero, sem rede, sem o /home real) construído a partir da
        MESMA configuração do PD (flake: nixosConfigurations.tupi-st = módulos do PD + ajustes de container).
  SEC = sistema de resgate independente (volta gerações/snapshots sem tocar no /home).

Fluxo: st_stage_change -> cópia da configuração (/etc/nixos) em STATE/work, aplica a mudança em
nixos/pd/agent-config.json (+ agent-extra.nix), constrói o ST e o PD com a mudança, liga o ST com ela e
verifica (unidades com falha novas, serviço alvo, programas novos). st_diff mostra o que muda.
st_promote aplica no PD EXATAMENTE o sistema construído no ensaio, com ponto de retorno (geração anterior
+ snapshot Btrfs quando houver) — e o Desfazer volta a geração. st_discard desliga o ST e apaga o ensaio.

Limite honesto: o container usa o MESMO kernel do PD. sysctl e parâmetros de boot não são ensaiados nele.

As operações privilegiadas (nix build, ligar o container, trocar a geração) passam por RUN, que o executor
liga a uma unidade transitória root na slice da IA (o PARAR IA encerra). Os testes trocam RUN/SYS.
"""
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid

from . import policy as P

FLAKE_REF = os.environ.get("TUPI_AGENT_FLAKE", "/etc/nixos#tupi-pd")
FLAKE_DIR, PD_ATTR = (FLAKE_REF.split("#", 1) + ["tupi-pd"])[:2]
ST_ATTR = os.environ.get("TUPI_ST_ATTR", "tupi-st")
CONTAINER = os.environ.get("TUPI_ST_CONTAINER", "tupi-st")
ST_ENABLED = os.environ.get("TUPI_ST_ENABLED", "0") == "1"
LIVE = os.environ.get("TUPI_AGENT_LIVE", "0") == "1"
CONFIG_REL = os.environ.get("TUPI_AGENT_CONFIG_REL", "nixos/pd")
STATE = os.path.join(os.environ.get("TUPI_AGENT_STATE", "/var/lib/tupi-agent"), "st")
ST_PROFILE = os.environ.get("TUPI_ST_PROFILE", f"/nix/var/nix/profiles/per-container/{CONTAINER}/system")
SYSTEM_PROFILE = os.environ.get("TUPI_SYSTEM_PROFILE", "/nix/var/nix/profiles/system")
CURRENT_SYSTEM = os.environ.get("TUPI_CURRENT_SYSTEM", "/run/current-system")
BUILD_TIMEOUT = int(os.environ.get("TUPI_ST_BUILD_TIMEOUT", "2400"))
BOOT_TIMEOUT = int(os.environ.get("TUPI_ST_BOOT_TIMEOUT", "300"))

LOCK = threading.RLock()

MSG_LIVE = ("ST indisponível no modo live (ISO): o ST só existe no TUPIOS instalado no disco. Instale o TUPIOS "
            "para ensaiar mudanças antes de aplicá-las. Agora, no live: set_kernel_param muda parâmetros do kernel "
            "de forma TEMPORÁRIA (some ao reiniciar), com Desfazer.")
KERNEL_NOTE = ("O ST é um container: usa o MESMO kernel do PD. Parâmetros do kernel (sysctl globais como vm.*, a "
               "maioria de kernel.*, e parâmetros de boot) NÃO podem ser ensaiados nele — só é verificado que a "
               "configuração constrói. Para testar o efeito: set_kernel_param (temporário, com Desfazer).")
EXPLANATION = ("PD = o sistema real do usuário. ST = ambiente de ensaio isolado (container com a mesma "
               "configuração do PD, sem a pasta pessoal real e sem rede) onde mudanças de sistema são testadas "
               "antes. SEC = resgate independente, que volta gerações/snapshots sem tocar no /home.")


def RUN(script, timeout):            # substituído pelo executor (root, slice da IA) e pelos testes
    raise RuntimeError("st.RUN não configurado")


def SYS(cmd, timeout=30):
    """Comando rápido e sem efeito colateral relevante (consultas ao systemd/nix)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


# ------------------------------------------------------------------ caminhos
def work():
    return os.path.join(STATE, "work")


def stage_file():
    return os.path.join(STATE, "stage.json")


def baseline_file():
    return os.path.join(STATE, "baseline.json")


def pd_link():
    return os.path.join(STATE, "pd-system")


def base_link():
    return os.path.join(STATE, "st-base")


def nix_home():
    return os.path.join(STATE, "nixhome")


def cfg_json(root):
    return os.path.join(root, CONFIG_REL, "agent-config.json")


def cfg_nix(root):
    return os.path.join(root, CONFIG_REL, "agent-extra.nix")


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ------------------------------------------------------------------ estado
def available():
    if LIVE:
        return False, MSG_LIVE
    if not ST_ENABLED:
        return False, ("O ST não está ativado nesta instalação (opção tupi.st.enable da configuração NixOS). "
                       "Sem ele, mudanças de sistema não podem ser ensaiadas antes.")
    if not os.path.isfile(os.path.join(FLAKE_DIR, "flake.nix")):
        return False, (f"A configuração do TUPIOS não está em {FLAKE_DIR} (flake.nix): sem ela não dá para "
                       "montar o ST nem aplicar mudanças.")
    return True, ""


def container_state():
    code, out = SYS(["systemctl", "is-active", f"container@{CONTAINER}.service"], 10)
    return "rodando" if out.strip() == "active" else ("ligando" if out.strip() == "activating" else "parado")


def load_stage():
    return load_json(stage_file(), None)


def brief(st):
    if not st:
        return None
    return {"id": st.get("id"), "descricao": st.get("descricao"), "resumo": P.change_summary(st.get("mudanca_total")),
            "verificacoes_ok": (st.get("verificacoes") or {}).get("ok"), "criado": st.get("criado"),
            "atualizado": st.get("atualizado")}


def status():
    ok, why = available()
    st = load_stage() if ok else None
    return {"disponivel": ok, "motivo": why, "container": container_state() if ok else "indisponível",
            "ensaio": brief(st), "kernel": KERNEL_NOTE, "o_que_e": EXPLANATION}


# ------------------------------------------------------- cópia de trabalho
IGNORE = shutil.ignore_patterns(".git", "result", "result-*", ".direnv")


def tree_files(root):
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in (".git", ".direnv") and not d.startswith("result"))
        for f in sorted(files):
            if f.startswith("result"):
                continue
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, root)
            try:
                if os.path.islink(p):
                    out[rel] = "link:" + os.readlink(p)
                else:
                    with open(p, "rb") as fh:
                        out[rel] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                out[rel] = "?"
    return out


def tree_hash(root):
    return hashlib.sha256(json.dumps(tree_files(root), sort_keys=True).encode()).hexdigest()


def changed_files(a, b):
    """Arquivos diferentes entre a (PD) e b (ensaio): [(rel, 'novo'|'alterado'|'removido')]."""
    fa, fb = tree_files(a), tree_files(b)
    out = []
    for rel in sorted(set(fa) | set(fb)):
        if rel not in fa:
            out.append((rel, "novo"))
        elif rel not in fb:
            out.append((rel, "removido"))
        elif fa[rel] != fb[rel]:
            out.append((rel, "alterado"))
    return out


def sync_work():
    if os.path.exists(work()):
        shutil.rmtree(work())
    os.makedirs(STATE, exist_ok=True)
    shutil.copytree(FLAKE_DIR, work(), ignore=IGNORE, symlinks=True)


# ------------------------------------------- configuração gerida pela IA
def empty_cfg():
    return {"pacotes": [], "sysctl": {}, "parametros_kernel": [], "extra": []}


def load_cfg(root):
    cfg = empty_cfg()
    cfg.update({k: v for k, v in load_json(cfg_json(root), {}).items() if k in cfg})
    return cfg


def render_extra(extra):
    if not extra:
        return None
    lines = ["# Gerado pelo Pajé (TUPIOS): mudanças ensaiadas no ST e aplicadas no PD com a sua aprovação.",
             "# Cada bloco é um módulo NixOS. Para desfazer use o app (Desfazer) ou apague o bloco.",
             "{ ... }:", "{", "  imports = ["]
    for e in extra:
        desc = re.sub(r"[\r\n]+", " ", str(e.get("descricao") or ""))[:200]
        lines.append(f"    # {e.get('id')}: {desc}")
        lines.append("    ({ config, lib, pkgs, ... }: {")
        for ln in str(e.get("nix") or "").strip().splitlines():
            lines.append("      " + ln)
        lines.append("    })")
    lines += ["  ];", "}", ""]
    return "\n".join(lines)


def write_cfg(root, cfg):
    data = {k: cfg[k] for k in ("pacotes", "sysctl", "parametros_kernel", "extra")}
    save_json(cfg_json(root), data)
    text = render_extra(cfg["extra"])
    if text:
        with open(cfg_nix(root), "w", encoding="utf-8") as f:
            f.write(text)
    elif os.path.exists(cfg_nix(root)):
        os.remove(cfg_nix(root))


def apply_change(cfg, change, change_id, descricao):
    notes = []
    for p in change.get("pacotes") or []:
        if p not in cfg["pacotes"]:
            cfg["pacotes"].append(p)
    for p in change.get("remover_pacotes") or []:
        if p in cfg["pacotes"]:
            cfg["pacotes"].remove(p)
        else:
            notes.append(f"{p} não foi instalado pela IA: pacotes da configuração base não são removidos por aqui")
    for k, v in (change.get("sysctl") or {}).items():
        cfg["sysctl"][k] = v
    for p in change.get("parametros_kernel") or []:
        if p not in cfg["parametros_kernel"]:
            cfg["parametros_kernel"].append(p)
    if change.get("nix"):
        cfg["extra"].append({"id": change_id, "descricao": descricao, "nix": str(change["nix"])})
    return notes


def merge_change(total, change):
    total = json.loads(json.dumps(total or {}))
    for k in ("pacotes", "remover_pacotes", "parametros_kernel"):
        if change.get(k):
            total[k] = list(dict.fromkeys((total.get(k) or []) + list(change[k])))
    if change.get("sysctl"):
        total.setdefault("sysctl", {}).update(change["sysctl"])
    if change.get("nix"):
        total["nix"] = ((total.get("nix") or "") + "\n" + str(change["nix"])).strip()
    return total


# -------------------------------------------------------- operações root
def q(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def build(attr, link_or_profile, profile=False, root=None):
    """Constrói nixosConfigurations.<attr> da cópia de trabalho (ou de root). -> (código, saída, toplevel)."""
    flag = "--profile" if profile else "--out-link"
    src = root or work()
    script = f"""set -o pipefail
mkdir -p {q(nix_home())} {q(os.path.dirname(link_or_profile))}
export HOME={q(nix_home())}
nix --extra-experimental-features 'nix-command flakes' build {flag} {q(link_or_profile)} \\
  {q('path:' + src + '#nixosConfigurations.' + attr + '.config.system.build.toplevel')} 2>&1 | tail -n 60
rc=${{PIPESTATUS[0]}}
[ $rc -eq 0 ] && echo "TOPLEVEL=$(readlink -f {q(link_or_profile)})"
exit $rc"""
    code, out = RUN(script, BUILD_TIMEOUT)
    m = re.search(r"^TOPLEVEL=(\S+)$", out, re.M)
    return code, out, (m.group(1) if m and code == 0 else None)


def boot_container(top):
    """Liga o ST com o sistema `top` (perfil do container já aponta para ele). -> (ok, falhas, saída)."""
    script = f"""nix-env -p {q(ST_PROFILE)} --set {q(top)} >/dev/null 2>&1
systemctl restart {q('container@' + CONTAINER + '.service')} || {{ echo "ST_START_FAIL"; journalctl -u {q('container@' + CONTAINER)} -n 20 --no-pager; exit 1; }}
for i in $(seq 1 {BOOT_TIMEOUT}); do nixos-container run {q(CONTAINER)} -- true >/dev/null 2>&1 && break; sleep 1; done
echo "ST_STATE=$(timeout {BOOT_TIMEOUT} nixos-container run {q(CONTAINER)} -- systemctl is-system-running --wait 2>/dev/null)"
echo "===FALHAS"
nixos-container run {q(CONTAINER)} -- systemctl list-units --failed --plain --no-legend --no-pager 2>/dev/null | awk '{{print $1}}'
echo "===FIM\""""
    code, out = RUN(script, BOOT_TIMEOUT * 2 + 120)
    if code != 0 or "ST_START_FAIL" in out:
        return False, [], out
    m = re.search(r"===FALHAS\n(.*?)===FIM", out, re.S)
    failed = [ln.strip() for ln in (m.group(1) if m else "").splitlines() if ln.strip()]
    return True, failed, out


def container_run(cmd, timeout):
    script = f"timeout {int(timeout)} nixos-container run {q(CONTAINER)} -- bash -lc {q(cmd)}"
    return RUN(script, timeout + 30)


def ensure_container():
    """ST ligado (com o ensaio atual, ou com a configuração atual do PD). -> (ok, mensagem)."""
    if container_state() == "rodando":
        return True, ""
    st = load_stage()
    top = (st or {}).get("st_top")
    if not top or not os.path.exists(top):
        base = load_json(baseline_file(), {}).get("top")
        if base and os.path.exists(base) and not st:
            top = base
        else:
            with LOCK:
                if not st:
                    sync_work()
                code, out, top = build(ST_ATTR, base_link())
            if not top:
                return False, "não consegui construir o ST:\n" + out[-3000:]
    ok, _failed, out = boot_container(top)
    return ok, ("" if ok else "o ST não ligou:\n" + out[-3000:])


def package_presence(names):
    """-> {nome: 'presente'|'novo'|'inexistente'} (presente = já está no sistema atual do PD)."""
    if not names:
        return {}
    lines = [f"export HOME={q(nix_home())}; mkdir -p {q(nix_home())}",
             f"CLOSURE=$(nix-store -qR {q(CURRENT_SYSTEM)})"]
    for n in names:
        ref = q(f"path:{work()}#nixosConfigurations.{PD_ATTR}.pkgs.{n}.outPath")
        lines.append(f"if p=$(nix --extra-experimental-features 'nix-command flakes' eval --raw {ref} 2>/dev/null); then "
                     f"if printf '%s\\n' \"$CLOSURE\" | grep -qxF \"$p\"; then echo 'PKG {n} presente'; "
                     f"else echo 'PKG {n} novo'; fi; else echo 'PKG {n} inexistente'; fi")
    code, out = RUN("\n".join(lines), BUILD_TIMEOUT)
    res = {}
    for n in names:
        m = re.search(rf"^PKG {re.escape(n)} (\w+)$", out, re.M)
        res[n] = m.group(1) if m else "inexistente"
    return res


# ------------------------------------------------------------------ diff
def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def unit_map(top):
    d = os.path.join(top, "etc", "systemd", "system")
    out = {}
    try:
        for name in os.listdir(d):
            if name.endswith((".service", ".timer", ".socket", ".mount", ".path", ".target")):
                p = os.path.join(d, name)
                out[name] = os.path.realpath(p)
    except OSError:
        pass
    return out


def text_diff(a, b, label_a, label_b):
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), label_a, label_b))


def compute_diff(pd_top):
    cur = os.path.realpath(CURRENT_SYSTEM)
    files = changed_files(FLAKE_DIR, work())
    cfg_diff = []
    for rel, kind in files:
        a = read_text(os.path.join(FLAKE_DIR, rel)) if kind != "novo" else ""
        b = read_text(os.path.join(work(), rel)) if kind != "removido" else ""
        cfg_diff.append(text_diff(a, b, f"PD/{rel}", f"ensaio/{rel}"))
    code, closures = SYS(["nix", "--extra-experimental-features", "nix-command", "store", "diff-closures", cur,
                          pd_top], 120)
    closures = re.sub(r"\x1b\[[0-9;]*m", "", closures).strip() if code == 0 else f"(não foi possível comparar: {closures.strip()[:300]})"
    ua, ub = unit_map(cur), unit_map(pd_top)
    services = {"novos": sorted(set(ub) - set(ua)), "removidos": sorted(set(ua) - set(ub)),
                "alterados": sorted(n for n in set(ua) & set(ub) if ua[n] != ub[n])}
    sysctl = text_diff(read_text(os.path.join(cur, "etc/sysctl.d/60-nixos.conf")),
                       read_text(os.path.join(pd_top, "etc/sysctl.d/60-nixos.conf")), "PD/sysctl", "ensaio/sysctl")
    kernel = text_diff(read_text(os.path.join(cur, "kernel-params")) + "\n",
                       read_text(os.path.join(pd_top, "kernel-params")) + "\n", "PD/boot", "ensaio/boot")
    return {"arquivos": [{"arquivo": r, "tipo": k} for r, k in files], "configuracao": "\n".join(cfg_diff).strip(),
            "pacotes": closures or "(nenhuma versão de pacote muda)", "servicos": services,
            "sysctl": sysctl.strip(), "parametros_boot": kernel.strip()}


def diff_text(d, limit=6000):
    parts = []
    if d.get("configuracao"):
        parts.append("== Configuração (arquivos) ==\n" + d["configuracao"])
    parts.append("== Pacotes (versões que entram/saem) ==\n" + (d.get("pacotes") or ""))
    s = d.get("servicos") or {}
    srv = []
    for k in ("novos", "removidos", "alterados"):
        if s.get(k):
            srv.append(f"{k}: " + ", ".join(s[k][:20]) + (" …" if len(s[k]) > 20 else ""))
    parts.append("== Serviços ==\n" + ("\n".join(srv) or "nenhum serviço muda"))
    if d.get("sysctl"):
        parts.append("== Parâmetros do kernel (sysctl) ==\n" + d["sysctl"])
    if d.get("parametros_boot"):
        parts.append("== Parâmetros de boot ==\n" + d["parametros_boot"])
    text = "\n\n".join(parts)
    return text if len(text) <= limit else text[:limit] + "\n[… diff cortado]"


# ------------------------------------------------------------- ensaio
def new_binaries(base_top, top):
    def bins(t):
        try:
            return set(os.listdir(os.path.join(t, "sw", "bin")))
        except OSError:
            return set()
    return sorted(bins(top) - bins(base_top))


def stage(change, descricao=""):
    """-> (código, resultado). Cumulativo: várias mudanças formam um ensaio até promover ou descartar."""
    ok, why = available()
    if not ok:
        return 3, {"error": why, "st_disponivel": False, "onde": "ST"}
    change = {k: v for k, v in (change or {}).items() if v}
    if not change:
        return 2, {"error": "nenhuma mudança informada (pacotes, remover_pacotes, sysctl, parametros_kernel ou nix)"}
    with LOCK:
        st = load_stage()
        fresh = st is None
        if fresh:
            sync_work()
            st = {"id": uuid.uuid4().hex[:10], "criado": time.time(), "base_hash": tree_hash(FLAKE_DIR),
                  "mudancas": [], "mudanca_total": {}, "descricao": descricao}
        notes = []
        # pacotes que o PD já tem: não há o que ensaiar
        if change.get("pacotes"):
            pres = package_presence(change["pacotes"])
            missing = [n for n, s in pres.items() if s == "inexistente"]
            if missing:
                if fresh:
                    shutil.rmtree(work(), ignore_errors=True)
                return 2, {"error": "pacote não encontrado no nixpkgs: " + ", ".join(missing), "onde": "ST"}
            have = [n for n, s in pres.items() if s == "presente"]
            if have:
                notes.append("já estão instalados no PD (nada a mudar): " + ", ".join(have))
                change["pacotes"] = [n for n in change["pacotes"] if n not in have]
                if not change["pacotes"]:
                    change.pop("pacotes")
            if not change:
                if fresh:
                    shutil.rmtree(work(), ignore_errors=True)
                return 0, {"ok": True, "nada_a_fazer": True, "mensagem": "; ".join(notes), "onde": "ST"}
        # linha de base: o que já falha no ST sem a mudança (para não culpar a mudança)
        baseline = load_json(baseline_file(), {})
        if fresh:
            code, out, base_top = build(ST_ATTR, base_link())
            if not base_top:
                shutil.rmtree(work(), ignore_errors=True)
                return 1, {"error": "a configuração ATUAL do PD não constrói no ST (nada foi mudado)",
                           "saida": out[-4000:], "onde": "ST"}
            if baseline.get("top") != base_top:
                booted, failed, out = boot_container(base_top)
                if not booted:
                    shutil.rmtree(work(), ignore_errors=True)
                    return 1, {"error": "o ST não ligou com a configuração atual", "saida": out[-4000:], "onde": "ST"}
                baseline = {"top": base_top, "falhas": failed, "quando": time.time()}
                save_json(baseline_file(), baseline)
            st["base_top"] = base_top
        before = load_cfg(work())
        cfg = json.loads(json.dumps(before))
        change_id = uuid.uuid4().hex[:8]
        notes += apply_change(cfg, change, change_id, descricao)
        write_cfg(work(), cfg)
        code, out, st_top = build(ST_ATTR, ST_PROFILE, profile=True)
        pd_out, pd_top = "", None
        if st_top:
            code, pd_out, pd_top = build(PD_ATTR, pd_link())
        if not (st_top and pd_top):
            write_cfg(work(), before)            # a mudança não constrói: volta a cópia ao estado anterior
            if fresh:
                shutil.rmtree(work(), ignore_errors=True)
            return 1, {"error": "a configuração com a mudança NÃO construiu; nada foi aplicado",
                       "saida": (out + "\n" + pd_out)[-5000:], "onde": "ST"}
        booted, failed, boot_out = boot_container(st_top)
        base_failed = set(baseline.get("falhas") or [])
        checks = {"st_ligou": booted,
                  "unidades_com_falha_novas": [u for u in failed if u not in base_failed],
                  "unidades_com_falha_ja_existentes": sorted(set(failed) & base_failed)}
        target = str(change.get("servico_alvo") or "").strip()
        if booted and target:
            unit = target if "." in target else target + ".service"
            c, o = container_run(f"systemctl is-active {q(unit)}", 30)
            checks["servico_alvo"] = {"unidade": unit, "estado": o.strip().splitlines()[-1] if o.strip() else "?"}
        bins = new_binaries(st.get("base_top") or "", st_top)
        if booted and bins:
            found = []
            for b in bins[:5]:
                c, o = container_run(f"command -v {q(b)}", 30)
                found.append({"programa": b, "encontrado_no_st": c == 0})
            checks["programas_novos"] = found
        if change.get("sysctl") or change.get("parametros_kernel"):
            checks["kernel"] = ("NÃO ensaiado: " + KERNEL_NOTE)
        checks["ok"] = bool(booted and not checks["unidades_com_falha_novas"]
                            and (not target or checks.get("servico_alvo", {}).get("estado") == "active")
                            and all(p["encontrado_no_st"] for p in checks.get("programas_novos", [])))
        d = compute_diff(pd_top)
        st["mudancas"].append({"id": change_id, "descricao": descricao, "mudanca": change, "quando": time.time()})
        st["mudanca_total"] = merge_change(st.get("mudanca_total"), change)
        st["descricao"] = "; ".join(m["descricao"] or P.change_summary(m["mudanca"]) for m in st["mudancas"])
        st.update(st_top=st_top, pd_top=pd_top, verificacoes=checks, diff=d, atualizado=time.time())
        save_json(stage_file(), st)
        res = {"ok": True, "onde": "ST", "ensaio": st["id"], "mudanca": P.change_summary(change),
               "verificacoes": checks, "notas": notes,
               "diff_resumo": diff_text(d, 2500),
               "proximo_passo": "Mostre o resultado ao usuário (st_diff) e, se ele quiser, use st_promote "
                                "(sempre pede aprovação) ou st_discard."}
        return (0 if checks["ok"] else 1), res


def current_diff():
    ok, why = available()
    if not ok:
        return 3, {"error": why, "onde": "ST"}
    st = load_stage()
    if not st:
        return 2, {"error": "não há mudança em ensaio no ST", "onde": "ST"}
    d = compute_diff(st["pd_top"]) if os.path.exists(st.get("pd_top") or "") else st.get("diff") or {}
    return 0, {"onde": "ST", "ensaio": st["id"], "descricao": st.get("descricao"),
               "verificacoes": st.get("verificacoes"), "diff": d, "texto": diff_text(d)}


def discard():
    ok, why = available()
    if not ok:
        return 3, {"error": why, "onde": "ST"}
    with LOCK:
        st = load_stage()
        RUN(f"systemctl stop {q('container@' + CONTAINER + '.service')} 2>/dev/null; true", 120)
        if os.path.exists(stage_file()):
            os.remove(stage_file())
        shutil.rmtree(work(), ignore_errors=True)
    return 0, {"ok": True, "onde": "ST", "descartado": brief(st) if st else None,
               "mensagem": "ST desligado e ensaio descartado; o PD não mudou." if st else "não havia ensaio"}


# ------------------------------------------------------------ promoção
def apply_system(new_top, snap_name):
    """Troca o PD para new_top (snapshot Btrfs + geração anterior guardada). -> (código, saída, antigo, snapshot)."""
    # o "antes" é o sistema que está RODANDO (o perfil pode nem existir, ou apontar para outra geração
    # depois de um nixos-rebuild boot): é para ele que o Desfazer volta
    old = os.path.realpath(CURRENT_SYSTEM)
    script = f"""SNAP=""
if [ "$(findmnt -n -o FSTYPE /)" = btrfs ] && command -v tupi-snapshot >/dev/null; then
  tupi-snapshot create {q(snap_name)} >/dev/null 2>&1 && SNAP={q(snap_name)}
fi
echo "SNAPSHOT=$SNAP"
nix-env -p {q(SYSTEM_PROFILE)} --set {q(new_top)} || exit 10
{q(new_top + '/bin/switch-to-configuration')} switch 2>&1 | tail -n 40
exit ${{PIPESTATUS[0]}}"""
    code, out = RUN(script, BUILD_TIMEOUT)
    m = re.search(r"^SNAPSHOT=(\S*)$", out, re.M)
    return code, out, old, (m.group(1) if m and m.group(1) else None)


def switch_back(old):
    script = (f"nix-env -p {q(SYSTEM_PROFILE)} --set {q(old)} && "
              f"{q(old + '/bin/switch-to-configuration')} switch 2>&1 | tail -n 30; exit ${{PIPESTATUS[0]}}")
    return RUN(script, BUILD_TIMEOUT)


def backup_files(rels, action_dir):
    saved = []
    for rel, kind in rels:
        src = os.path.join(FLAKE_DIR, rel)
        item = {"arquivo": rel, "existia": os.path.lexists(src)}
        if item["existia"] and os.path.isfile(src):
            dst = os.path.join(action_dir, "arquivos", rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            item["copia"] = dst
        saved.append(item)
    return saved


def restore_files(saved):
    for item in saved:
        dst = os.path.join(FLAKE_DIR, item["arquivo"])
        if item.get("copia"):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(item["copia"], dst)
        elif not item["existia"] and os.path.lexists(dst):
            os.remove(dst)


def git_track(rels):
    if os.path.isdir(os.path.join(FLAKE_DIR, ".git")):
        paths = [r for r, k in rels if k != "removido"]
        if paths:
            SYS(["git", "-C", FLAKE_DIR, "add", "--intent-to-add", "--"] + paths, 30)


def promote(action_dir):
    """Aplica no PD exatamente o sistema construído no ensaio. -> (código, resultado, undo|None)."""
    ok, why = available()
    if not ok:
        return 3, {"error": why, "onde": "PD"}, None
    with LOCK:
        st = load_stage()
        if not st:
            return 2, {"error": "não há mudança em ensaio no ST: use st_stage_change antes", "onde": "PD"}, None
        if tree_hash(FLAKE_DIR) != st.get("base_hash"):
            return 2, {"error": f"a configuração do PD ({FLAKE_DIR}) mudou depois do ensaio: descarte (st_discard) "
                                "e ensaie de novo", "onde": "PD"}, None
        new_top = st.get("pd_top")
        if not new_top or not os.path.exists(new_top):
            return 2, {"error": "o sistema construído no ensaio não existe mais: ensaie de novo", "onde": "PD"}, None
        rels = changed_files(FLAKE_DIR, work())
        saved = backup_files(rels, action_dir)
        for rel, kind in rels:                       # a configuração do PD passa a ter a mudança
            src, dst = os.path.join(work(), rel), os.path.join(FLAKE_DIR, rel)
            if kind == "removido":
                if os.path.lexists(dst):
                    os.remove(dst)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        git_track(rels)
        code, out, old, snap = apply_system(new_top, f"antes-promover-{st['id']}")
        if code not in (0, 4):                       # 4 = ativado, mas alguma unidade falhou ao iniciar
            back_code, back_out = switch_back(old) if code != 10 else (0, "")
            restore_files(saved)
            return 1, {"error": "a troca do sistema falhou; voltei para a geração anterior", "saida": out[-4000:],
                       "volta": back_out[-1500:], "onde": "PD"}, None
        undo = {"kind": "promote", "old_system": old, "new_system": new_top, "files": saved,
                "descricao": st.get("descricao")}
        RUN(f"systemctl stop {q('container@' + CONTAINER + '.service')} 2>/dev/null; true", 120)
        os.remove(stage_file())
        shutil.rmtree(work(), ignore_errors=True)
        failed_code, failed = SYS(["systemctl", "list-units", "--failed", "--plain", "--no-legend", "--no-pager"], 20)
        res = {"ok": True, "onde": "PD", "aplicado": st.get("descricao"), "geracao_anterior": old,
               "geracao_nova": new_top, "snapshot_btrfs": snap,
               "aviso": ("algumas unidades falharam ao iniciar: " + failed.strip()[:500]) if code == 4 else None,
               "desfazer": "Desfazer volta para a geração anterior e restaura os arquivos de configuração.",
               "saida": out[-2000:]}
        return 0, res, undo


def undo_promote(undo):
    code, out = switch_back(undo["old_system"])
    if code in (0, 4):
        restore_files(undo.get("files") or [])
        git_track([(f["arquivo"], "alterado") for f in undo.get("files") or [] if f.get("copia")])
    return code in (0, 4), {"geracao": undo["old_system"], "saida": out[-2000:]}
