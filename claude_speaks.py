#!/usr/bin/env python3
"""claude-speaks : Claude Code lit ses reponses a voix haute, avec une voix clonee.
Tout tourne sur ton Mac Apple Silicon. Pas d'API, pas de compte, zero token.
Tuto : https://a-foutoyet.github.io/claude-speaks/

    python claude_speaks.py transcrire    ecrit voix.txt a partir de voix.wav (Whisper)
    python claude_speaks.py say "Salut"   fait parler (lance le serveur si besoin)
    python claude_speaks.py stop          coupe la phrase en cours
    python claude_speaks.py off | on      coupe ou remet la lecture automatique
    python claude_speaks.py quitter       arrete le serveur et libere la memoire
    python claude_speaks.py hook          point d'entree du hook Stop de Claude Code
    python claude_speaks.py serve         le serveur (lance tout seul)

Dans Claude Code, avec la commande /parole : /parole off, /parole on, /parole stop.
"""
import json
import os
import pathlib
import queue
import re
import socket
import subprocess
import sys
import threading
import time

# ---- reglages ----------------------------------------------------------------
LANGUE = "french"      # french english german italian portuguese spanish japanese korean russian chinese
MODELE = "mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16"
WHISPER = "openai/whisper-large-v3-turbo"   # sert seulement a la commande transcrire
TEMPERATURE = 0.6      # plus bas = voix plus reguliere, plus haut = plus vivante mais moins stable
# ------------------------------------------------------------------------------

DOSSIER = pathlib.Path.home() / ".claude-speaks"
VOIX = DOSSIER / "voix.wav"             # ton enregistrement, mono 24 kHz
TEXTE_VOIX = DOSSIER / "voix.txt"       # les mots exacts prononces dans voix.wav
SOCK = DOSSIER / "speaks.sock"
VERROU = DOSSIER / "serveur.lock"
LOG = DOSSIER / "serveur.log"
MUET = DOSSIER / "muted"
DERNIERE = DOSSIER / "derniere"         # derniere lecture (wav + txt), pour analyser un rate
PYTHON = DOSSIER / "venv" / "bin" / "python"
SCRIPT = pathlib.Path(__file__).resolve()


# ---- le texte : on garde ce qui se lit bien a l'oral -------------------------

def pour_oral(txt: str) -> str:
    txt = re.sub(r"(?ms)^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[^\n]*$", " ", txt)   # blocs de code
    txt = re.sub(r"(?s)(?:^|\n)[ \t]*(?:`{3,}|~{3,}).*", " ", txt)              # bloc pas ferme
    lignes, garde, i = txt.splitlines(), [], 0
    sep = re.compile(r"^[ \t]*[|:\- \t]*-[|:\- \t]*$")
    while i < len(lignes):                  # tableau = en-tete + ligne ---|--- + lignes a la suite
        if ("|" in lignes[i] and i + 1 < len(lignes) and "|" in lignes[i + 1]
                and sep.match(lignes[i + 1])):
            i += 2
            while i < len(lignes) and "|" in lignes[i] and lignes[i].strip():
                i += 1
            continue
        garde.append(lignes[i])
        i += 1
    txt = "\n".join(l for l in garde if not re.match(r"^\s*[-*_]{3,}\s*$", l))
    txt = re.sub(r"</?[A-Za-z][^<>\n]*>", " ", txt)                      # balises HTML
    txt = re.sub(r"!?\[([^\]\n]{0,300})\]\([^)\n]{0,500}\)", r"\1", txt)   # liens : on garde le texte
    txt = re.sub(r"https?://\S+", " ", txt)
    txt = re.sub(r"`([^`\n]*)`",                                         # chemins et noms de fichiers
                 lambda m: " " if re.search(r"[/\\{}=<>$~]|\.\w{1,4}\b", m.group(1)) else m.group(1), txt)
    txt = re.sub(r"[*_#>`]+", " ", txt)
    txt = re.sub(r"^[ \t]*(?:[-+]|\d+\.)[ \t]+", "", txt, flags=re.M)
    txt = re.sub(r"[^\w\s.,;:!?'\"()%€$-]", " ", txt)                    # emojis et symboles
    txt = re.sub(r"[ \t]+", " ", txt)
    return re.sub(r"[ \t]+([.,;:!?])", r"\1", txt).strip()


def blocs(txt: str, maxi: int = 600) -> list:
    """Genere la reponse par gros blocs, pas phrase par phrase :
    une generation par phrase fait repartir l'intonation de zero a chaque fois."""
    out, buf = [], ""
    for s in re.split(r"(?<=[.!?])\s+|\n+", txt):
        s = s.strip()
        if not s:
            continue
        if buf and len(buf) + len(s) > maxi:
            out.append(buf)
            buf = ""
        buf = f"{buf} {s}".strip()
    return out + ([buf] if buf else [])


# ---- transcription de ton enregistrement ---------------------------------------

def transcrire() -> None:
    """Le texte de voix.txt doit coller mot pour mot a voix.wav, sinon la voix deraille.
    Whisper l'ecrit pour toi, en local. Relis-le quand meme."""
    if not VOIX.exists():
        sys.exit("Pas de voix.wav : fais d'abord la conversion avec ffmpeg (etape 5).")
    os.environ["HF_HUB_OFFLINE"] = "1"
    from mlx_audio.stt import load
    texte = load(WHISPER).generate(str(VOIX), language=None).text.strip()   # langue detectee seule
    TEXTE_VOIX.write_text(texte + "\n")
    print(texte)
    print("\nC'est ecrit dans ~/.claude-speaks/voix.txt. Relis-le : un mot faux fait derailler la voix.")


# ---- le serveur : garde le modele en memoire ----------------------------------

def serve() -> None:
    import fcntl
    verrou = open(VERROU, "w")
    try:
        fcntl.flock(verrou, fcntl.LOCK_EX | fcntl.LOCK_NB)  # un seul serveur a la fois
    except BlockingIOError:
        return
    os.environ["HF_HUB_OFFLINE"] = "1"      # jamais en ligne : le modele est deja telecharge
    import numpy as np
    import sounddevice as sd
    from mlx_audio.tts.utils import load_model
    from scipy.io import wavfile

    model = load_model(MODELE)
    sr = model.sample_rate
    ref = (str(VOIX), TEXTE_VOIX.read_text().strip())
    taches: queue.Queue = queue.Queue()
    audio: queue.Queue = queue.Queue(maxsize=64)
    courant = [0]

    def generer(texte):
        plafond = min(4096, 100 + 2 * len(texte))   # ~12 tokens par seconde d'audio
        for r in model.generate(texte, ref_audio=ref[0], ref_text=ref[1], lang_code=LANGUE,
                                stream=True, streaming_interval=0.5, temperature=TEMPERATURE,
                                max_tokens=plafond):
            yield np.array(r.audio, dtype=np.float32)

    for _ in generer("Salut."):             # chauffe : met la voix en cache
        pass

    def producteur():
        while True:
            mon_id, texte = taches.get()
            morceaux = []
            for bloc in blocs(texte):
                for m in generer(bloc):
                    if courant[0] != mon_id:
                        break
                    audio.put((mon_id, m))
                    morceaux.append(m)
                if courant[0] != mon_id:
                    break
            if morceaux:
                wavfile.write(f"{DERNIERE}.wav", sr, np.concatenate(morceaux))
                pathlib.Path(f"{DERNIERE}.txt").write_text(texte)

    def lecteur():
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as sortie:
            while True:
                mon_id, m = audio.get()
                if mon_id == courant[0]:
                    sortie.write(m.reshape(-1, 1))

    threading.Thread(target=producteur, daemon=True).start()
    threading.Thread(target=lecteur, daemon=True).start()

    if SOCK.exists():
        SOCK.unlink()                       # reste d'un plantage, on tient le verrou
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(SOCK))
    os.chmod(SOCK, 0o600)
    srv.listen(4)
    print("pret", flush=True)
    while True:
        conn, _ = srv.accept()
        with conn:
            try:
                req = json.loads(conn.makefile().readline() or "{}")
                assert isinstance(req, dict)
            except (ValueError, AssertionError):
                continue
            if req.get("cmd") in ("say", "stop"):
                courant[0] += 1             # coupe la lecture en cours
                for q in (taches, audio):
                    while not q.empty():
                        q.get_nowait()
            if req.get("cmd") == "say" and req.get("text"):
                taches.put((courant[0], req["text"]))
            conn.sendall(b"ok\n")


# ---- le client ----------------------------------------------------------------

def envoyer(req: dict) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(str(SOCK))
            s.sendall((json.dumps(req) + "\n").encode())
            return s.makefile().readline().strip() == "ok"
    except OSError:
        return False


def dire(texte: str, auto: bool = False) -> None:
    if not texte or (auto and MUET.exists()):
        return
    if not envoyer({"cmd": "ping"}):
        with open(LOG, "a") as log:
            subprocess.Popen([str(PYTHON), str(SCRIPT), "serve"], stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(480):                # le chargement du modele prend quelques secondes
            time.sleep(0.25)
            if envoyer({"cmd": "ping"}):
                break
    if auto and MUET.exists():            # coupe pendant le chargement du modele
        return
    envoyer({"cmd": "say", "text": texte})


def derniere_reponse(transcript: str) -> str:
    """Ce que Claude a ecrit apres son dernier appel d'outil : la reponse finale."""
    textes = []
    for ligne in open(transcript, encoding="utf-8"):
        try:
            e = json.loads(ligne)
        except ValueError:
            continue
        contenu = (e.get("message") or {}).get("content")
        if e.get("type") == "user":
            textes = []
        elif e.get("type") == "assistant" and isinstance(contenu, list):
            textes += [b.get("text", "") for b in contenu if b.get("type") == "text"]
    return "\n".join(textes)


def hook() -> None:
    """Rend la main tout de suite et n'echoue jamais : la parole part dans un autre process."""
    try:
        if MUET.exists() or not (VOIX.exists() and TEXTE_VOIX.exists()):
            return
        evt = json.loads(sys.stdin.read() or "{}")
        texte = evt.get("last_assistant_message") or ""
        if not texte and os.path.exists(evt.get("transcript_path", "")):
            texte = derniere_reponse(evt["transcript_path"])
        texte = pour_oral(texte[:20000])
        if texte:
            p = subprocess.Popen([str(PYTHON), str(SCRIPT), "_dire"], stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            p.stdin.write(texte.encode())
            p.stdin.close()
    except Exception as e:
        print(f"claude-speaks : {e}", file=sys.stderr)


if __name__ == "__main__":
    os.umask(0o077)
    DOSSIER.mkdir(exist_ok=True)
    os.chmod(DOSSIER, 0o700)                # ta voix et tes reponses ne regardent que toi
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "serve":
        serve()
    elif cmd == "hook":
        hook()
    elif cmd == "transcrire":
        transcrire()
    elif cmd == "_dire":
        dire(sys.stdin.read(), auto=True)
    elif cmd == "say":
        import wave
        try:
            with wave.open(str(VOIX)) as w:
                duree = w.getnframes() / w.getframerate()
            if duree < 8:                   # trop court : le modele se met a baragouiner
                print(f"Attention : ton enregistrement fait {duree:.0f} s. Vise 10 a 30 s, "
                      "sinon la voix peut inventer des sons.")
        except (OSError, wave.Error):
            print("Pas de voix : fais l'etape 5 du tuto (voix.wav et voix.txt).")
            sys.exit(1)
        dire(pour_oral(" ".join(sys.argv[2:])))
    elif cmd == "stop":
        envoyer({"cmd": "stop"})
    elif cmd == "off":
        MUET.touch()
        envoyer({"cmd": "stop"})
        print("Lecture automatique coupee.")
    elif cmd == "on":
        MUET.unlink(missing_ok=True)
        print("Lecture automatique remise.")
    elif cmd == "quitter":
        subprocess.run(["pkill", "-f", f"{SCRIPT} serve"])
        print("Serveur arrete, memoire liberee. Il repart tout seul a la prochaine reponse.")
    else:
        print(__doc__)
