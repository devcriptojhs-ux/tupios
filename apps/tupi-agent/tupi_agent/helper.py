"""Ajudante que roda DENTRO da unidade isolada de cada ferramenta da IA.

Uso: helper.py list CAMINHO | read CAMINHO MAX_BYTES | write CAMINHO overwrite|append (conteúdo na entrada)
     | remove CAMINHO | info | screenshot PASTA
Responde sempre JSON na saída padrão. Código de saída 0 = sucesso.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time


def out(obj, code=0):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()
    sys.exit(code)


def fail(msg, code=1):
    out({"error": msg}, code)


def cmd_sysctl(path, value):
    """Grava um parâmetro do kernel (a unidade só deixa ESTE caminho de /proc/sys gravável)."""
    if not path.startswith("/proc/sys/") or ".." in path:
        fail("caminho de parâmetro inválido", 2)
    name = path[len("/proc/sys/"):].replace("/", ".")
    try:
        with open(path) as f:
            before = f.read().strip()
        with open(path, "w") as f:
            f.write(value)
        with open(path) as f:
            after = f.read().strip()
    except OSError as e:
        fail(f"não foi possível mudar {name}: {e.strerror or e}", 1)
    out({"parametro": name, "antes": before, "agora": after,
         "aplicado": " ".join(after.split()) == " ".join(value.split())})


def cmd_list(path):
    try:
        entries = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                    kind = "pasta" if stat.S_ISDIR(st.st_mode) else "link" if stat.S_ISLNK(st.st_mode) else "arquivo"
                    entries.append({"nome": e.name, "tipo": kind, "tamanho": st.st_size,
                                    "modificado": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))})
                except OSError:
                    entries.append({"nome": e.name, "tipo": "?"})
        entries.sort(key=lambda x: (x.get("tipo") != "pasta", x["nome"].lower()))
        total = len(entries)
        out({"pasta": path, "total": total, "itens": entries[:500], "cortado": total > 500})
    except PermissionError:
        fail(f"Sem permissão para listar {path} (pasta privada ou protegida).", 13)
    except FileNotFoundError:
        fail(f"A pasta {path} não existe.", 2)
    except NotADirectoryError:
        fail(f"{path} não é uma pasta.", 20)


def cmd_read(path, max_bytes):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            data = f.read(int(max_bytes))
    except PermissionError:
        fail(f"Sem permissão para ler {path} (arquivo privado ou protegido).", 13)
    except FileNotFoundError:
        fail(f"O arquivo {path} não existe.", 2)
    except IsADirectoryError:
        fail(f"{path} é uma pasta (use list_dir).", 21)
    if b"\x00" in data[:4096]:
        out({"arquivo": path, "tamanho": size, "binario": True,
             "conteudo": "(arquivo binário: o conteúdo não é texto)"})
    out({"arquivo": path, "tamanho": size, "conteudo": data.decode("utf-8", "replace"),
         "cortado": size > len(data)})


def cmd_write(path, mode):
    data = sys.stdin.buffer.read()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "ab" if mode == "append" else "wb") as f:
            f.write(data)
    except PermissionError:
        fail(f"Sem permissão para gravar em {path}.", 13)
    except OSError as e:
        fail(f"Não foi possível gravar {path}: {e.strerror}", 1)
    out({"ok": True, "arquivo": path, "bytes": len(data), "modo": mode})


def cmd_remove(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        fail(f"Não foi possível remover {path}: {e.strerror}")
    out({"ok": True, "removido": path})


def cmd_info():
    info = {}
    try:
        info["carga"] = list(os.getloadavg())
        info["cpus"] = os.cpu_count()
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    info["cpu"] = line.split(":", 1)[1].strip()
                    break
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0])
        info["memoria_mb"] = {"total": mem.get("MemTotal", 0) // 1024, "disponivel": mem.get("MemAvailable", 0) // 1024}
        du = shutil.disk_usage("/")
        info["disco_raiz_gb"] = {"total": round(du.total / 1e9, 1), "livre": round(du.free / 1e9, 1)}
        with open("/proc/uptime") as f:
            info["ligado_ha_horas"] = round(float(f.read().split()[0]) / 3600, 1)
        ps = subprocess.run(["ps", "-eo", "pid,user,%cpu,%mem,comm", "--sort=-%cpu"], capture_output=True,
                            text=True, timeout=10).stdout.splitlines()
        info["processos_top"] = ps[:11]
    except Exception as e:  # informação parcial é melhor que nada
        info["aviso"] = str(e)
    out(info)


def cmd_screenshot(folder):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("print-ia-%Y-%m-%d_%H-%M-%S.png"))
    for cmd in (["xfce4-screenshooter", "-f", "-s", path], ["import", "-window", "root", path],
                ["grim", path]):
        if shutil.which(cmd[0]):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if r.returncode == 0 and os.path.exists(path):
                out({"ok": True, "arquivo": path})
    fail("Não foi possível capturar a tela (nenhuma ferramenta de captura funcionou).")


# ------------------------------------------- usar o computador (mouse/teclado)
SHOT_MAX = (1280, 800)        # o print vai para a IA nesse tamanho; as coordenadas dela são NESSA imagem
BUTTONS = {"click": ("1", 1), "double_click": ("1", 2), "right_click": ("3", 1), "middle_click": ("2", 1)}
SCROLL = {"up": "4", "down": "5", "left": "6", "right": "7"}


def xdo(*args, timeout=30):
    try:
        r = subprocess.run([os.environ.get("TUPI_XDOTOOL", "xdotool"), *[str(a) for a in args]], capture_output=True,
                           text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"xdotool {args[0] if args else ''} não respondeu em {timeout}s")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "xdotool falhou").strip()[:300])
    return r.stdout


def pointer():
    v = dict(line.split("=", 1) for line in xdo("getmouselocation", "--shell").split() if "=" in line)
    return int(v["X"]), int(v["Y"])


def window_under_pointer():
    """A janela que está DE FATO sob o ponteiro (a de cima, considerando o empilhamento). Pode ser a própria
    janela do programa ou a moldura que o gerenciador de janelas pôs em volta dela."""
    v = dict(line.split("=", 1) for line in xdo("getmouselocation", "--shell").split() if "=" in line)
    return v.get("WINDOW")


def geometry(wid):
    g = dict(line.split("=", 1) for line in xdo("getwindowgeometry", "--shell", wid).split() if "=" in line)
    return int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"])


def is_paje_window(wid, wins):
    """A janela de cima é do Pajé: é uma delas, ou é a moldura justa em volta de uma delas (barra de título e
    bordas). Uma janela de outro programa por cima do Pajé NÃO conta (ela não envolve a do Pajé)."""
    if not wid or not wins:
        return False
    if wid in wins:
        return True
    try:
        gx, gy, gw, gh = geometry(wid)
    except (RuntimeError, KeyError, ValueError):
        return False
    for (rx, ry, rw, rh) in wins.values():
        wraps = gx - 2 <= rx and gy - 2 <= ry and rx + rw <= gx + gw + 2 and ry + rh <= gy + gh + 2
        if wraps and gw - rw <= 40 and gh - rh <= 80:
            return True
    return False


def xprop(*args):
    exe = os.environ.get("TUPI_XPROP") or shutil.which("xprop")
    if not exe:
        return ""
    try:
        return subprocess.run([exe, *args], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def top_window_at(x, y):
    """A janela visível de cima no ponto (x, y), pela ordem de empilhamento que o gerenciador de janelas publica
    (_NET_CLIENT_LIST_STACKING, de baixo para cima), contando a moldura (_NET_FRAME_EXTENTS). None se não der
    para saber (sem gerenciador de janelas EWMH)."""
    out = xprop("-root", "_NET_CLIENT_LIST_STACKING")
    if "#" not in out:
        return None
    order = [str(int(h, 16)) for h in re.findall(r"0x[0-9a-fA-F]+", out.split("#", 1)[1])]
    try:
        visible = set(xdo("search", "--onlyvisible", "--classname", ".").split())
    except RuntimeError:
        visible = set(order)
    for wid in reversed(order):
        if wid not in visible:
            continue
        try:
            gx, gy, gw, gh = geometry(wid)
        except (RuntimeError, KeyError, ValueError):
            continue
        m = re.search(r"=\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)", xprop("-id", wid, "_NET_FRAME_EXTENTS"))
        left, right, top, bottom = (int(v) for v in m.groups()) if m else (0, 0, 0, 0)
        if gx - left <= x < gx + gw + right and gy - top <= y < gy + gh + bottom:
            return wid
    return None


def paje_at(x, y, wins):
    """O ponto (x, y) cai numa janela do Pajé visível por cima? Duas conferências (basta uma dizer que sim):
    a ordem de empilhamento do gerenciador de janelas e a janela sob o ponteiro (leva o ponteiro até lá)."""
    if not wins:
        return False
    if top_window_at(x, y) in wins:
        return True
    xdo("mousemove", "--sync", x, y)
    return is_paje_window(window_under_pointer(), wins)


def focused_windows():
    """Janelas que podem receber o teclado: a ativa (gerenciador de janelas) E a que tem o foco de verdade
    (XGetInputFocus) — sem gerenciador de janelas só a segunda existe."""
    ids = []
    for args in (("getactivewindow",), ("getwindowfocus",)):
        try:
            w = xdo(*args).strip()
            if w and w not in ids:
                ids.append(w)
        except RuntimeError:
            pass
    return ids


def active_window():
    for wid in focused_windows():
        try:
            return wid, xdo("getwindowname", wid).strip()[:120]
        except RuntimeError:
            continue
    return None, ""


def paje_windows():
    """Janelas do Pajé (aprovações, permissões, bolinha, faixa de controle): a IA não clica nem digita nelas."""
    try:
        ids = xdo("search", "--onlyvisible", "--class", "tupi-assistant").split()
    except RuntimeError:
        return {}
    rects = {}
    for wid in ids:
        try:
            g = dict(line.split("=", 1) for line in xdo("getwindowgeometry", "--shell", wid).split() if "=" in line)
            rects[wid] = (int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"]))
        except (RuntimeError, KeyError, ValueError):
            continue
    return rects


def cmd_desktop():
    req = json.loads(sys.stdin.read() or "{}")
    action = req.get("action")
    try:
        W, H = (int(v) for v in xdo("getdisplaygeometry").split()[:2])
    except (RuntimeError, ValueError) as e:
        fail(f"sem acesso à tela: {e}", 2)
    s = min(1.0, SHOT_MAX[0] / W, SHOT_MAX[1] / H)
    iw, ih = round(W * s), round(H * s)

    def real(kx, ky):
        x, y = int(req[kx]), int(req[ky])
        if not (0 <= x <= iw and 0 <= y <= ih):
            fail(f"({x}, {y}) está fora da imagem da tela ({iw}x{ih})", 2)
        return min(W - 1, round(x / s)), min(H - 1, round(y / s))

    def img(p):
        return [round(p[0] * s), round(p[1] * s)]

    def done(**extra):
        time.sleep(0.35)
        p = pointer()
        _, title = active_window()
        out(dict({"ok": True, "cursor": img(p), "pos_real": list(p), "janela_ativa": title,
                  "tela_imagem": [iw, ih]}, **extra))

    if action == "screenshot":
        folder = os.path.join(os.path.expanduser("~"), ".cache", "tupios-ia", "tela")
        os.makedirs(folder, exist_ok=True)
        old = sorted(f for f in os.listdir(folder) if f.startswith("tela-"))
        for f in old[:-30]:
            try:
                os.unlink(os.path.join(folder, f))
            except OSError:
                pass
        path = os.path.join(folder, time.strftime("tela-%Y%m%d-%H%M%S-") + str(int(time.time() * 1000) % 1000) + ".png")
        magick = os.environ.get("TUPI_MAGICK", "magick")
        r = subprocess.run([magick, "import", "-silent", "-window", "root", "-resize", f"{iw}x{ih}!", path],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(path):
            fail("não consegui capturar a tela: " + (r.stderr or r.stdout).strip()[:300])
        _, title = active_window()
        out({"ok": True, "arquivo": path, "largura": iw, "altura": ih, "tela_real": [W, H],
             "cursor": img(pointer()), "janela_ativa": title,
             "coordenadas": f"use x de 0 a {iw - 1} e y de 0 a {ih - 1}, medidos nesta imagem"})
    if action == "wait":
        time.sleep(max(0.0, min(10.0, float(req.get("seconds") or 1))))
        done()
    # liberado "nesta conversa": o ponteiro saiu de onde a IA deixou? então foi o usuário -> devolve o controle
    exp = req.get("esperado")
    if exp:
        x, y = pointer()
        if abs(x - int(exp[0])) > 3 or abs(y - int(exp[1])) > 3:
            out({"assumiu": True, "error": "o usuário mexeu no mouse"}, 5)
    wins = paje_windows()
    if action in ("type", "key"):
        # sem foco definido o teclado vai para a janela sob o ponteiro: confere as duas
        px, py = pointer()
        top = top_window_at(px, py)
        if (set(focused_windows()) & set(wins) or top in wins
                or is_paje_window(window_under_pointer(), wins)):
            fail("A janela em foco é do Pajé (aprovações e permissões): a IA não digita nela. Clique antes na "
                 "janela onde quer escrever.", 4)
        # o teclado vai para a janela onde a IA clicou (a de cima sob o ponteiro): se o foco saiu dela (ex.: o
        # Pajé acabou de sair da frente e o gerenciador de janelas passou o foco adiante), devolve o foco a ela
        if top and top not in focused_windows():
            try:
                xdo("windowactivate", "--sync", top, timeout=5)
                time.sleep(0.2)
            except RuntimeError:
                pass
            if set(focused_windows()) & set(wins):
                fail("O foco ficou numa janela do Pajé: a IA não digita nela.", 4)
    pts = []
    if action in BUTTONS or action in ("move", "scroll", "drag"):
        pts.append(real("x", "y"))
    if action == "drag":
        pts.append(real("x2", "y2"))
    try:
        blocked = any(paje_at(px, py, wins) for (px, py) in pts)
    except RuntimeError as e:
        fail(f"não consegui conferir a janela sob o ponteiro: {e}")
    if blocked:
        fail("Esse ponto fica numa janela do Pajé (aprovações, permissões ou a bolinha): a IA não clica nelas.", 4)
    try:
        if action == "move":
            xdo("mousemove", "--sync", *pts[0])
        elif action in BUTTONS:
            button, times = BUTTONS[action]
            xdo("mousemove", "--sync", *pts[0], "click", "--repeat", times, "--delay", 120, button)
        elif action == "scroll":
            n = max(1, min(15, int(req.get("amount") or 3)))
            xdo("mousemove", "--sync", *pts[0], "click", "--repeat", n, "--delay", 60,
                SCROLL.get(req.get("direction"), "5"))
        elif action == "drag":
            (x1, y1), (x2, y2) = pts
            xdo("mousemove", "--sync", x1, y1, "mousedown", "1", "sleep", "0.25", "mousemove", "--sync", x2, y2,
                "sleep", "0.25", "mouseup", "1")
        elif action == "type":
            text = str(req.get("text") or "")
            xdo("type", "--clearmodifiers", "--delay", 12, "--", text, timeout=max(30, len(text) // 20))
        elif action == "key":
            xdo("key", "--clearmodifiers", "--delay", 80, "--", *str(req.get("keys") or "").split())
        else:
            fail(f"ação desconhecida: {action}", 2)
    except RuntimeError as e:
        fail(f"a ação falhou: {e}")
    done()


def main():
    a = sys.argv[1:]
    if not a:
        fail("uso: helper.py list|read|write|remove|info|screenshot ...", 64)
    cmd = a[0]
    if cmd == "list" and len(a) == 2:
        cmd_list(a[1])
    elif cmd == "read" and len(a) == 3:
        cmd_read(a[1], a[2])
    elif cmd == "write" and len(a) == 3:
        cmd_write(a[1], a[2])
    elif cmd == "remove" and len(a) == 2:
        cmd_remove(a[1])
    elif cmd == "info":
        cmd_info()
    elif cmd == "screenshot" and len(a) == 2:
        cmd_screenshot(a[1])
    elif cmd == "desktop" and len(a) == 1:
        cmd_desktop()
    elif cmd == "sysctl" and len(a) == 3:
        cmd_sysctl(a[1], a[2])
    fail(f"comando inválido: {' '.join(a)}", 64)


if __name__ == "__main__":
    main()
