#!/usr/bin/env python3
"""claude-speaks: Claude Code reads its replies out loud, in a cloned voice.

Everything runs locally on an Apple Silicon Mac (Qwen3-TTS through mlx-audio).
No API, no account, no tokens used.

    claude-speaks say "text"         speak now (interrupts the current reading)
    claude-speaks stop               stop talking
    claude-speaks on | off           turn automatic reading on or off
    claude-speaks status             current state
    claude-speaks voice              list voices
    claude-speaks voice <name>       switch voice
    claude-speaks voice add <name> <audio> "<exact transcript>" [<audio2> "<transcript2>" ...]
    claude-speaks model 0.6B | 1.7B  switch model
    claude-speaks lang <language>    reading language
    claude-speaks hook install | uninstall
    claude-speaks hook               Claude Code Stop hook entry point (reads stdin)
    claude-speaks serve              the server (started automatically when needed)
"""
import fcntl
import json
import math
import os
import pathlib
import queue
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time

HOME = pathlib.Path(os.environ.get("CLAUDE_SPEAKS_HOME", pathlib.Path.home() / ".claude-speaks"))
CONF = HOME / "config.json"
LOG = HOME / "server.log"
LOCK = HOME / "server.lock"
HOOK_STATE = HOME / "hook-state.json"   # which settings.json containers we created
VOICES = HOME / "voices"
HISTORY = HOME / "history"          # copy of the last readings (wav + timestamped text)
KEEP = 30
PY = HOME / "venv" / "bin" / "python"
SCRIPT = pathlib.Path(__file__).resolve()
CLAUDE_DIR = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR", pathlib.Path.home() / ".claude"))
SETTINGS = CLAUDE_DIR / "settings.json"
MODELS = {"0.6B": "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16",
          "1.7B": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-bf16"}
LANGUAGES = ["english", "french", "german", "italian", "portuguese", "spanish",
             "japanese", "korean", "russian", "chinese"]
DEFAULTS = {"voice": "", "model": "0.6B", "lang": "english", "auto": True, "temperature": 0.6}
# Our hook command, and only ours: '"/path/to/claude-speaks" hook' or 'claude-speaks hook'.
OUR_HOOK = re.compile(r"""^\s*(?:(['"])(?:[^'"]*/)?claude-speaks\1|(?:\S*/)?claude-speaks)\s+hook\s*$""")
START_WAIT = 120                    # seconds a background delivery waits for the server


def socket_path() -> pathlib.Path:
    """macOS caps socket paths at 104 bytes. Past that, fall back to the per-user
    temp dir (mode 0700, readable by nobody else), never to the shared /tmp."""
    p = HOME / "speaks.sock"
    if len(str(p)) <= 100:
        return p
    import hashlib
    tmp = os.environ.get("TMPDIR") or subprocess.run(
        ["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True).stdout.strip()
    return pathlib.Path(tmp) / f"claude-speaks-{hashlib.sha1(str(HOME).encode()).hexdigest()[:8]}.sock"


SOCK = socket_path()


def ensure_home() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(HOME, 0o700)               # voices and reply history are private


def conf() -> dict:
    try:
        return {**DEFAULTS, **json.loads(CONF.read_text())}
    except Exception:
        return dict(DEFAULTS)


def save_conf(c: dict) -> None:
    ensure_home()
    CONF.write_text(json.dumps(c, ensure_ascii=False, indent=2))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "voice"


# ------------------------------------------------------------------ voices

def list_voices() -> list:
    if not VOICES.exists():
        return []
    return sorted(d.name for d in VOICES.iterdir()
                  if (d / "ref.wav").exists() and (d / "ref.txt").exists())


def find_voice(fragment: str) -> list:
    exact = [v for v in list_voices() if v == slug(fragment)]
    return exact or [v for v in list_voices() if slug(fragment) in v]


def reference(name: str):
    d = VOICES / name
    if not (d / "ref.wav").exists():
        raise RuntimeError(f"voice not found: {name} (run: claude-speaks voice)")
    return str(d / "ref.wav"), (d / "ref.txt").read_text().strip()


def add_voice(name: str, files: list, transcripts: list) -> str:
    """Turns one or more recordings into a single mono 24 kHz reference."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is missing: brew install ffmpeg")
    for f in files:
        if not pathlib.Path(f).is_file():
            raise RuntimeError(f"file not found: {f}")
    ensure_home()
    d = VOICES / slug(name)
    d.mkdir(parents=True, exist_ok=True)
    lst = d / "sources.txt"
    lst.write_text("".join("file '%s'\n" % str(pathlib.Path(f).resolve()).replace("'", "'\\''")
                           for f in files))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-ac", "1", "-ar", "24000", str(d / "ref.wav")], check=True)
    lst.unlink()
    (d / "ref.txt").write_text(" ".join(t.strip() for t in transcripts) + "\n")
    return d.name


# ------------------------------------------------------------------ text

def for_speech(txt: str) -> str:
    """Drops what does not read well out loud."""
    txt = re.sub(r"(?ms)^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", " ", txt)   # fenced code blocks
    txt = re.sub(r"(?s)(```|~~~).*", " ", txt)                           # unclosed fence
    txt = re.sub(r"<[^>\n]+>", " ", txt)                                  # HTML tags
    lines = txt.splitlines()
    drop = set()
    sep = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")
    for i, l in enumerate(lines):                  # a table = header, separator, rows
        if sep.match(l):
            j = i - 1
            while j >= 0 and "|" in lines[j]:
                drop.add(j)
                j -= 1
            j = i
            while j < len(lines) and "|" in lines[j]:
                drop.add(j)
                j += 1
    txt = "\n".join(l for i, l in enumerate(lines)
                    if i not in drop and l.count("|") < 2
                    and not re.match(r"^\s*[-*_]{3,}\s*$", l))  # tables, horizontal rules
    txt = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", txt)                 # links: keep the text
    txt = re.sub(r"https?://\S+", " ", txt)
    txt = re.sub(r"`([^`]*)`",
                 lambda m: " " if re.search(r"[/\\{}=<>$]|\.\w{1,4}\b", m.group(1)) else m.group(1),
                 txt)                                                     # paths and commands
    txt = re.sub(r"[*_#>]+", " ", txt)
    txt = re.sub(r"^\s*(?:[-+]|\d+\.)\s+", "", txt, flags=re.M)
    txt = re.sub(r"[^\w\s.,;:!?'\"()%€$-]", " ", txt)                    # emojis and symbols
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s+([.,;:!?])", r"\1", txt)
    return txt.strip()


def sentences(txt: str) -> list:
    parts = re.split(r"(?<=[.!?:;])\s+|\n+", txt)
    out, buf = [], ""
    for p in (x.strip() for x in parts):
        if not p:
            continue
        buf = f"{buf} {p}".strip()
        if len(buf) >= 25:
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def blocks(txt: str, limit: int = 600) -> list:
    """Large blocks generated in one pass. One generation per sentence restarts
    the intonation at every sentence and the voice keeps changing."""
    out, buf = [], ""
    for s in sentences(txt):
        if buf and len(buf) + len(s) > limit:
            out.append(buf)
            buf = ""
        buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    return out


# ------------------------------------------------------------------ server

def model_cached(repo: str) -> bool:
    try:
        from huggingface_hub import try_to_load_from_cache
        return isinstance(try_to_load_from_cache(repo, "config.json"), str)
    except Exception:
        return False


def serve() -> None:
    ensure_home()
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)   # one server per install
    except BlockingIOError:
        return
    c = conf()
    if not c["voice"] or c["voice"] not in list_voices():
        raise SystemExit("no voice configured: claude-speaks voice add <name> <audio> \"<transcript>\"")
    repo = MODELS[c["model"]]
    if model_cached(repo):
        os.environ["HF_HUB_OFFLINE"] = "1"               # no network call once the model is here
    import numpy as np
    import sounddevice as sd
    from mlx_audio.tts.utils import load_model

    model = load_model(repo)
    sr = model.sample_rate
    state = {"voice": c["voice"], "ref": reference(c["voice"])}
    tasks: queue.Queue = queue.Queue()
    audio: queue.Queue = queue.Queue(maxsize=64)
    gen_id = [0]
    t_req = [0.0]
    played = [-1]
    mutex = threading.Lock()

    def generate(text, voice):
        ref_audio, ref_text = voice
        for r in model.generate(text, ref_audio=ref_audio, ref_text=ref_text,
                                lang_code=c["lang"], stream=True, streaming_interval=0.5,
                                temperature=float(c["temperature"])):
            yield np.array(r.audio, dtype=np.float32)

    for _ in generate("Hello.", state["ref"]):          # caches the voice reference
        pass

    def archive(chunks, marks):
        from scipy.io import wavfile
        HISTORY.mkdir(exist_ok=True)
        name = time.strftime("%Y-%m-%d_%H-%M-%S")
        wavfile.write(HISTORY / f"{name}.wav", sr, np.concatenate(chunks))
        (HISTORY / f"{name}.txt").write_text(
            f"voice {state['voice']}, model {c['model']}\n\n" +
            "\n".join(f"{t // 60:02.0f}:{t % 60:04.1f}  {b}" for t, b in marks))
        for old in sorted(HISTORY.glob("*.wav"))[:-KEEP]:
            old.unlink()
            old.with_suffix(".txt").unlink(missing_ok=True)

    def producer():
        while True:
            my_id, text = tasks.get()
            chunks, marks, t = [], [], 0.0
            for block in blocks(text):
                marks.append((t, block))
                for chunk in generate(block, state["ref"]):
                    if gen_id[0] != my_id:
                        break
                    audio.put((my_id, chunk))
                    chunks.append(chunk)
                    t += len(chunk) / sr
                if gen_id[0] != my_id:
                    break
            if chunks:
                try:
                    archive(chunks, marks)
                except Exception as e:
                    print(f"archive: {e}", flush=True)

    def player():
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as out:
            while True:
                my_id, chunk = audio.get()
                if my_id == gen_id[0]:
                    if played[0] != my_id:
                        played[0] = my_id
                        print(f"first sound {time.time() - t_req[0]:.2f}s after the request", flush=True)
                    out.write(chunk.reshape(-1, 1))

    def flush():
        for q in (tasks, audio):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass

    threading.Thread(target=producer, daemon=True).start()
    threading.Thread(target=player, daemon=True).start()

    if SOCK.exists() or SOCK.is_symlink():
        SOCK.unlink()                  # stale socket from a crash; we hold the lock
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    os.chmod(SOCK, 0o600)
    srv.listen(4)
    print(f"ready: {c['model']}, voice {state['voice']}", flush=True)
    while True:
        conn, _ = srv.accept()
        try:
            req = json.loads(conn.makefile().readline() or "{}")
            cmd = req.get("cmd")
            with mutex:
                if cmd in ("say", "stop"):
                    gen_id[0] += 1
                    flush()
                if cmd == "say" and req.get("text"):
                    t_req[0] = time.time()
                    tasks.put((gen_id[0], req["text"]))
                elif cmd == "voice":
                    state["ref"] = reference(req["name"])
                    state["voice"] = req["name"]
                    for _ in generate("Hello.", state["ref"]):
                        pass
            conn.sendall(b"ok\n")
        except Exception as e:
            conn.sendall(f"err {e}\n".encode())
        finally:
            conn.close()


# ------------------------------------------------------------------ client

def socket_is_ours() -> bool:
    try:
        st = os.lstat(SOCK)
    except FileNotFoundError:
        return False
    import stat as st_mod
    return st_mod.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid()


def send(req: dict, timeout: float = 30) -> str:
    if not socket_is_ours():
        return "server not running"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(str(SOCK))
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode())
        reply = s.makefile().readline().strip()
        s.close()
        return reply
    except (ConnectionRefusedError, FileNotFoundError, socket.timeout):
        return "server not running"


def start_server(wait: float) -> bool:
    """Starts the server if needed and waits up to `wait` seconds for it."""
    ensure_home()
    if send({"cmd": "ping"}, timeout=2) == "ok":
        return True
    with open(LOG, "a") as log:
        subprocess.Popen([str(PY), str(SCRIPT), "serve"], stdout=log, stderr=log,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    end = time.time() + wait
    while time.time() < end:
        time.sleep(0.25)
        if send({"cmd": "ping"}, timeout=2) == "ok":
            return True
    return False


def say(text: str, wait: float) -> str:
    if not text:
        return "nothing to say"
    if not start_server(wait):
        return f"the server did not start, see {LOG}"
    return send({"cmd": "say", "text": text})


def last_reply(transcript: str) -> str:
    """The text Claude wrote after its last tool call."""
    texts = []
    for line in open(transcript, encoding="utf-8"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "user":             # user message or tool result
            texts = []
        elif e.get("type") == "assistant" and isinstance(content, list):
            texts += [b["text"] for b in content if b.get("type") == "text"]
    return "\n".join(texts)


def hook() -> int:
    """Stop hook. Returns in milliseconds, never fails, prints nothing:
    the speaking happens in a detached process."""
    try:
        c = conf()
        if not c.get("auto") or c.get("voice") not in list_voices():
            return 0
        ev = json.loads(sys.stdin.read() or "{}")
        text = ev.get("last_assistant_message") or ""
        path = ev.get("transcript_path", "")
        if not text and path and os.path.exists(path):
            text = last_reply(path)
        text = for_speech(text)
        if text:
            p = subprocess.Popen([str(PY), str(SCRIPT), "_deliver"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            p.stdin.write(text.encode())
            p.stdin.close()
    except Exception as e:
        print(f"claude-speaks: {e}", file=sys.stderr)
    return 0


# ------------------------------------------------------------------ settings.json

def _strict_load(raw: str):
    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate keys")
        return dict(pairs)

    def finite(s):
        v = float(s)
        if math.isinf(v):
            raise ValueError("out-of-range number")
        return v

    def no_const(s):
        raise ValueError(f"non-standard value {s}")

    return json.loads(raw, object_pairs_hook=no_dupes, parse_float=finite, parse_constant=no_const)


def _is_ours(h: dict) -> bool:
    return h.get("type") == "command" and bool(OUR_HOOK.match(h.get("command", "")))


def manage_hook(action: str) -> str:
    """Adds or removes our single entry in hooks.Stop. Every other key and hook
    stays as it was. Backup first, atomic write."""
    raw = SETTINGS.read_text() if SETTINGS.exists() else "{}"
    try:
        d = _strict_load(raw)
    except ValueError as e:
        raise SystemExit(f"{SETTINGS} was left untouched ({e}). Add the hook by hand, see the README.")
    hooks = d.get("hooks", {})
    stop = hooks.get("Stop", [])
    present = any(_is_ours(h) for g in stop for h in g.get("hooks", []))
    if action == "install" and present:
        return "hook already installed"
    if action == "uninstall" and not present:
        return "no hook to remove"

    if action == "install":
        target = os.environ.get("CLAUDE_SPEAKS_BIN") or shutil.which("claude-speaks") or "claude-speaks"
        cmd = f'"{target}" hook' if re.fullmatch(r"[\w./ -]+", target) else f"{shlex.quote(target)} hook"
        # Remember which containers we create, so uninstall removes only those.
        created = {"hooks": "hooks" not in d,
                   "stop": "Stop" not in d.get("hooks", {})}
        ensure_home()
        HOOK_STATE.write_text(json.dumps(created))
        d.setdefault("hooks", {}).setdefault("Stop", []).append(
            {"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
    else:
        kept = []
        for g in stop:
            rest = [h for h in g.get("hooks", []) if not _is_ours(h)]
            if rest:
                kept.append({**g, "hooks": rest})
            elif not g.get("hooks"):
                kept.append(g)          # an empty group that was already there
        try:
            created = json.loads(HOOK_STATE.read_text())
        except Exception:
            created = {"hooks": False, "stop": False}   # unknown: leave containers in place
        hooks["Stop"] = kept
        if not kept and created.get("stop"):
            hooks.pop("Stop")
        if not hooks and created.get("hooks"):
            d.pop("hooks")
        HOOK_STATE.unlink(missing_ok=True)

    if SETTINGS.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = SETTINGS.with_name(f"settings.json.before-claude-speaks-{stamp}")
        n = 1
        while backup.exists():
            n += 1
            backup = SETTINGS.with_name(f"settings.json.before-claude-speaks-{stamp}-{n}")
        shutil.copy2(SETTINGS, backup)
    indent = 4 if re.search(r'^\{\n    "', raw) else 2
    target = SETTINGS.resolve() if SETTINGS.is_symlink() else SETTINGS   # keep dotfile symlinks
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".settings.json.claude-speaks-{os.getpid()}")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=indent) + "\n")
    if target.exists():
        os.chmod(tmp, target.stat().st_mode & 0o777)
    os.replace(tmp, target)
    return f"hook {'installed' if action == 'install' else 'removed'} in {SETTINGS}"


def restart_server() -> None:
    send({"cmd": "stop"}, timeout=2)
    subprocess.run(["pkill", "-f", f"{SCRIPT} serve"])


# ------------------------------------------------------------------ CLI

def main() -> int:
    os.umask(0o077)
    a = sys.argv[1:] or ["status"]
    cmd, rest = a[0], a[1:]
    c = conf()
    if cmd == "serve":
        serve()
    elif cmd == "_deliver":
        say(sys.stdin.read(), wait=START_WAIT)
    elif cmd == "hook":
        if rest and rest[0] in ("install", "uninstall"):
            print(manage_hook(rest[0]))
            return 0
        return hook()
    elif cmd == "say":
        print(say(for_speech(" ".join(rest)), wait=START_WAIT))
    elif cmd == "stop":
        print(send({"cmd": "stop"}))
    elif cmd in ("on", "off"):
        c["auto"] = cmd == "on"
        save_conf(c)
        if cmd == "off":
            send({"cmd": "stop"}, timeout=2)
        print("automatic reading", cmd)
    elif cmd == "voice":
        if not rest:
            voices = list_voices()
            if not voices:
                print('no voice yet. Add one: claude-speaks voice add <name> <audio> "<transcript>"')
            for v in voices:
                print(f"  {v}{'  <- active' if v == c['voice'] else ''}")
        elif rest[0] == "add":
            if len(rest) < 4 or len(rest[2:]) % 2:
                print('usage: claude-speaks voice add <name> <audio> "<transcript>" [<audio2> "<transcript2>" ...]')
                return 1
            pairs = rest[2:]
            name = add_voice(rest[1], pairs[0::2], pairs[1::2])
            if not c["voice"]:
                c["voice"] = name
                save_conf(c)
            print(f"voice added: {name}")
        else:
            hits = find_voice(" ".join(rest))
            if len(hits) != 1:
                print("not found or ambiguous:", ", ".join(hits) or "no matching voice")
                return 1
            c["voice"] = hits[0]
            save_conf(c)
            reply = send({"cmd": "voice", "name": hits[0]}, timeout=60)
            print(f"active voice: {hits[0]}" + ("" if reply in ("ok", "server not running") else f" ({reply})"))
    elif cmd in ("model", "lang"):
        choices = MODELS if cmd == "model" else LANGUAGES
        if not rest or rest[0] not in choices:
            print(f"possible values: {', '.join(choices)}")
            return 1
        c[cmd] = rest[0]
        save_conf(c)
        restart_server()
        print(f"{cmd} = {rest[0]} (the server restarts on the next reading)")
    elif cmd == "status":
        running = send({"cmd": "ping"}, timeout=2) == "ok"
        print(f"voice {c['voice'] or 'none'}, model {c['model']}, language {c['lang']}, "
              f"auto {'on' if c['auto'] else 'off'}, server {'running' if running else 'stopped'}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
