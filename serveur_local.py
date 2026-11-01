"""
GameVault — Serveur local v4
- Téléchargement par chunks (morceaux) avec reprise
- Support Range requests (pause/reprise)
- Compression gzip automatique
- Statistiques de téléchargement
- Upload + catalogue + comptes
- Synchronisation WebDAV + maintenance locale
- Données utilisateur séparées des ressources installées
"""

import http.server
import socketserver
import json
import os
import threading
import time
import webbrowser
import shutil
import mimetypes
import subprocess
import sys
import zipfile
import io
from email import policy
from email.parser import BytesParser
from urllib.parse import unquote
from pathlib import Path
import gzip
import hashlib
import secrets
import gc
import platform
from urllib.parse import urlparse
from datetime import datetime

from cloud_sync import CloudSyncError, WebDAVSync

SITE_FOLDER    = os.path.dirname(os.path.abspath(__file__))
PORT           = int(os.environ.get("GAMEVAULT_PORT", "8080"))
# The HTTP API includes local account and administration actions. Keep it
# loopback-only even if a parent process or shell exports another bind host.
HOST           = "127.0.0.1"
_default_data  = os.path.join(os.environ.get("APPDATA", SITE_FOLDER), "GameVault", "data") if os.name == "nt" else SITE_FOLDER
DATA_FOLDER    = os.path.abspath(os.environ.get("GAMEVAULT_DATA_DIR", _default_data))
COMPTES_FILE   = os.path.join(DATA_FOLDER, "repertoire", "comptes.txt")
GAMES_FOLDER   = os.path.join(DATA_FOLDER, "games")
_default_library = os.path.join(os.path.dirname(DATA_FOLDER), "library") if os.path.basename(DATA_FOLDER).lower() == "data" else os.path.join(DATA_FOLDER, "library")
LIBRARY_FOLDER = os.path.abspath(os.environ.get("GAMEVAULT_LIBRARY_DIR", _default_library))
COVERS_FOLDER  = os.path.join(DATA_FOLDER, "covers")
CATALOGUE_FILE = os.path.join(DATA_FOLDER, "repertoire", "catalogue.txt")
STATS_FILE     = os.path.join(DATA_FOLDER, "repertoire", "stats.txt")
CLOUD_FILE     = os.path.join(DATA_FOLDER, "cloud.json")
CHUNK_SIZE     = 1024 * 512  # 512 Ko par chunk
MAX_JSON_BODY  = 1024 * 1024
# 0 means no application cap: uploads are streamed to disk and limited by free disk space.
MAX_UPLOAD_BYTES = max(0, int(os.environ.get("GAMEVAULT_MAX_UPLOAD_MB", "0"))) * 1024 * 1024
MAX_COVER_BYTES = 15 * 1024 * 1024
WRITE_LOCK     = threading.RLock()
cloud_sync     = WebDAVSync(DATA_FOLDER, CLOUD_FILE)


def atomic_write_text(path, content):
    """Write small metadata files without leaving a half-written catalogue."""
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    temporary = f"{path}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            try: os.unlink(temporary)
            except OSError: pass


def flat_text(value, limit=500):
    """Keep the legacy pipe-delimited metadata format unambiguous."""
    return str(value or "").replace("|", "¦").replace("\r", " ").replace("\n", " ").strip()[:limit]


class MultipartStream:
    """Small bounded-buffer multipart reader; file parts go straight to disk."""
    def __init__(self, stream, length):
        self.stream = stream
        self.remaining = length
        self.buffer = bytearray()

    def _fill(self):
        if self.remaining <= 0:
            return False
        chunk = self.stream.read(min(CHUNK_SIZE, self.remaining))
        if not chunk:
            return False
        self.remaining -= len(chunk)
        self.buffer.extend(chunk)
        return True

    def read_line(self, max_bytes=65536):
        while True:
            index = self.buffer.find(b"\r\n")
            if index >= 0:
                line = bytes(self.buffer[:index])
                del self.buffer[:index + 2]
                return line
            if len(self.buffer) > max_bytes or not self._fill():
                raise ValueError("En-tête multipart invalide")

    def read_exact(self, size):
        while len(self.buffer) < size:
            if not self._fill():
                raise ValueError("Multipart incomplet")
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def copy_until(self, marker, sink, limit=None):
        written = 0
        while True:
            index = self.buffer.find(marker)
            if index >= 0:
                chunk = bytes(self.buffer[:index])
                written += len(chunk)
                if limit is not None and written > limit:
                    raise OverflowError("Image trop volumineuse (maximum 15 Mo)")
                if chunk:
                    sink(chunk)
                del self.buffer[:index + len(marker)]
                return written

            safe_length = len(self.buffer) - len(marker) - 2
            if safe_length > 0:
                chunk = bytes(self.buffer[:safe_length])
                written += len(chunk)
                if limit is not None and written > limit:
                    raise OverflowError("Image trop volumineuse (maximum 15 Mo)")
                sink(chunk)
                del self.buffer[:safe_length]
            if not self._fill():
                raise ValueError("Fin de fichier multipart inattendue")


def read_multipart(stream, content_type, length, game_temp, cover_temp):
    content_header = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    boundary = content_header.get_param("boundary", header="content-type")
    if not boundary:
        raise ValueError("Boundary multipart manquant")

    reader = MultipartStream(stream, length)
    marker = b"\r\n--" + boundary.encode("ascii", errors="strict")
    if reader.read_line() != b"--" + boundary.encode("ascii"):
        raise ValueError("Début multipart invalide")

    fields, files = {}, {}
    while True:
        header_lines = []
        while True:
            line = reader.read_line()
            if not line:
                break
            header_lines.append(line)
        headers = BytesParser(policy=policy.default).parsebytes(b"\r\n".join(header_lines) + b"\r\n\r\n")
        disposition = headers.get("Content-Disposition", "")
        disposition_header = BytesParser(policy=policy.default).parsebytes(
            f"Content-Disposition: {disposition}\r\n\r\n".encode("utf-8")
        )
        name = disposition_header.get_param("name", header="content-disposition")
        filename = disposition_header.get_filename()
        if not name:
            raise ValueError("Champ multipart sans nom")

        if filename is not None:
            if name == "fichier":
                target, limit, kind = game_temp, None, "game"
            elif name == "image":
                target, limit, kind = cover_temp, MAX_COVER_BYTES, "image"
            else:
                raise ValueError("Champ de fichier inattendu")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as output:
                size = reader.copy_until(marker, output.write, limit)
            files[kind] = {"filename": os.path.basename(unquote(filename)), "size": size}
        else:
            value = io.BytesIO()
            size = reader.copy_until(marker, value.write, 16 * 1024)
            fields[name] = value.getvalue().decode("utf-8", errors="replace").strip()

        suffix = reader.read_exact(2)
        if suffix == b"--":
            if reader.buffer.startswith(b"\r\n"):
                reader.read_exact(2)
            break
        if suffix != b"\r\n":
            raise ValueError("Fin de champ multipart invalide")
    return fields, files

# ─────────────────────────────────────────
# COULEURS CONSOLE
# ─────────────────────────────────────────
class C:
    CYAN   = '\033[96m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    RED    = '\033[91m'
    WHITE  = '\033[97m'
    GRAY   = '\033[90m'
    BOLD   = '\033[1m'
    RESET  = '\033[0m'

def log(msg, level="INFO"):
    now = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": f"{C.CYAN}i", "OK": f"{C.GREEN}OK",
             "WARN": f"{C.YELLOW}!", "ERR": f"{C.RED}X",
             "UPLOAD": f"{C.YELLOW}UP", "DL": f"{C.GREEN}DL",
             "STAT": f"{C.CYAN}#"}
    icon = icons.get(level, "-")
    print(f"  {C.GRAY}[{now}]{C.RESET} {icon} {msg}{C.RESET}")

# ─────────────────────────────────────────
# STATS
# ─────────────────────────────────────────
download_stats = {}  # jeu_id -> nombre de téléchargements
active_sessions = {}
SESSION_TTL = 24 * 60 * 60


def hash_password(password):
    """Hash passwords with a salted PBKDF2 format while keeping migration easy."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 210_000)
    return "pbkdf2_sha256$210000${}${}".format(
        salt.hex(), digest.hex()
    )


def verify_password(password, stored):
    if not stored:
        return False
    if not stored.startswith("pbkdf2_sha256$"):
        # Legacy GameVault versions stored plaintext. This branch enables a
        # transparent migration on the first successful login.
        return secrets.compare_digest(password, stored)
    try:
        algorithm, rounds, salt_hex, digest_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(rounds)
        )
        return secrets.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def create_session(compte):
    now = time.time()
    for old_token, old_session in list(active_sessions.items()):
        if old_session.get("expires", 0) < now:
            active_sessions.pop(old_token, None)
    token = secrets.token_urlsafe(32)
    active_sessions[token] = {
        "id": compte["id"],
        "role": compte["role"],
        "expires": now + SESSION_TTL,
    }
    return token


def session_from_request(handler):
    header = handler.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    session = active_sessions.get(token)
    if not session:
        return None
    if session["expires"] < time.time():
        active_sessions.pop(token, None)
        return None
    session["expires"] = time.time() + SESSION_TTL
    return session

def charger_stats():
    global download_stats
    if not os.path.exists(STATS_FILE): return
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = line.split("|")
            if len(parts) == 2:
                try: download_stats[parts[0].strip()] = int(parts[1].strip())
                except: pass

def sauver_stats():
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with WRITE_LOCK:
        content = ["# GAMEVAULT - STATISTIQUES TELECHARGEMENTS", "# ID_JEU | NB_TELECHARGEMENTS", ""]
        content.extend(f"{jid} | {nb}" for jid, nb in download_stats.items())
        atomic_write_text(STATS_FILE, "\n".join(content) + "\n")

def incr_stat(jeu_id):
    download_stats[jeu_id] = download_stats.get(jeu_id, 0) + 1
    sauver_stats()

# ─────────────────────────────────────────
# COMPTES
# ─────────────────────────────────────────
def init_dossiers():
    os.makedirs(os.path.dirname(COMPTES_FILE), exist_ok=True)
    os.makedirs(GAMES_FOLDER, exist_ok=True)
    os.makedirs(COVERS_FOLDER, exist_ok=True)
    os.makedirs(LIBRARY_FOLDER, exist_ok=True)
    # In a packaged desktop build the static bundle is read-only and the
    # writable catalogue/games live in %APPDATA%/GameVault/data.
    if DATA_FOLDER != os.path.abspath(SITE_FOLDER):
        for folder in ("repertoire", "games"):
            source = os.path.join(SITE_FOLDER, folder)
            target = os.path.join(DATA_FOLDER, folder)
            if os.path.isdir(source):
                os.makedirs(target, exist_ok=True)
                for name in os.listdir(source):
                    src, dst = os.path.join(source, name), os.path.join(target, name)
                    if os.path.isfile(src) and not os.path.exists(dst):
                        shutil.copy2(src, dst)
    if not os.path.exists(COMPTES_FILE):
        with open(COMPTES_FILE, "w", encoding="utf-8") as f:
            f.write("# GAMEVAULT - COMPTES\n# ID | NOM | EMAIL | MOT_DE_PASSE | ROLE | DATE\n\n")
            f.write(f"admin | Admin | admin@gamevault.com | {hash_password('admin123')} | admin | {datetime.now().strftime('%d/%m/%Y %H:%M')}\n")
        log("Compte admin créé", "OK")
    if not os.path.exists(CATALOGUE_FILE):
        with open(CATALOGUE_FILE, "w", encoding="utf-8") as f:
            f.write("# GAMEVAULT - CATALOGUE\n# ID | TITRE | GENRE | DESCRIPTION | FICHIER | TAILLE | EMOJI | DATE\n\n")
        log("Catalogue créé", "OK")
    charger_stats()
    log(f"Dossier jeux : {GAMES_FOLDER}", "OK")
    log(f"Bibliothèque installée : {LIBRARY_FOLDER}", "OK")

def lire_comptes():
    comptes = []
    if not os.path.exists(COMPTES_FILE): return comptes
    with open(COMPTES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                comptes.append({"id": parts[0], "name": parts[1], "email": parts[2],
                                 "password": parts[3], "role": parts[4], "date": parts[5]})
    return comptes

def ecrire_comptes(comptes):
    with WRITE_LOCK:
        content = ["# GAMEVAULT - COMPTES", "# ID | NOM | EMAIL | MOT_DE_PASSE | ROLE | DATE", ""]
        content.extend(f"{c['id']} | {c['name']} | {c['email']} | {c['password']} | {c['role']} | {c['date']}" for c in comptes)
        atomic_write_text(COMPTES_FILE, "\n".join(content) + "\n")

def ajouter_compte(name, email, password, role="user"):
    name, email = flat_text(name, 120), flat_text(email, 240)
    comptes = lire_comptes()
    email = email.lower()
    if any(c["email"].lower() == email for c in comptes):
        return None, "Email déjà utilisé"
    compte = {"id": str(int(time.time() * 1000)), "name": name, "email": email,
              "password": hash_password(password), "role": role,
              "date": datetime.now().strftime("%d/%m/%Y %H:%M")}
    comptes.append(compte)
    ecrire_comptes(comptes)
    log(f"Nouveau compte : {name}", "OK")
    return compte, None

def connexion(email, password):
    email = str(email).strip().lower()
    comptes = lire_comptes()
    for c in comptes:
        if c["email"].lower() == email and verify_password(password, c["password"]):
            if not c["password"].startswith("pbkdf2_sha256$"):
                c["password"] = hash_password(password)
                ecrire_comptes(comptes)
            log(f"Connexion : {c['name']}", "OK")
            return c, None
    return None, "Email ou mot de passe incorrect"

def modifier_compte(compte_id, name=None, email=None, password=None):
    comptes = lire_comptes()
    for c in comptes:
        if c["id"] == compte_id:
            if email and any(other["id"] != compte_id and other["email"].lower() == str(email).strip().lower() for other in comptes):
                return None, "Email déjà utilisé"
            if name:     c["name"]     = flat_text(name, 120)
            if email:    c["email"]    = flat_text(email, 240).lower()
            if password: c["password"] = hash_password(password)
            ecrire_comptes(comptes)
            return c, None
    return None, "Compte introuvable"

def supprimer_compte(compte_id):
    comptes = [c for c in lire_comptes() if c["id"] != compte_id]
    ecrire_comptes(comptes)

# ─────────────────────────────────────────
# CATALOGUE
# ─────────────────────────────────────────
def lire_catalogue():
    jeux = []
    if not os.path.exists(CATALOGUE_FILE): return jeux
    with open(CATALOGUE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"): continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 8:
                jid = parts[0]
                jeux.append({"id": jid, "titre": parts[1], "genre": parts[2],
                              "description": parts[3], "fichier": parts[4],
                              "taille": parts[5], "emoji": parts[6], "date": parts[7],
                              "image": parts[8] if len(parts) > 8 else "",
                              "telechargements": download_stats.get(jid, 0)})
    return jeux

def ecrire_catalogue(jeux):
    with WRITE_LOCK:
        content = ["# GAMEVAULT - CATALOGUE", "# ID | TITRE | GENRE | DESCRIPTION | FICHIER | TAILLE | EMOJI | DATE | IMAGE", ""]
        content.extend(f"{j['id']} | {j['titre']} | {j['genre']} | {j['description']} | {j['fichier']} | {j['taille']} | {j['emoji']} | {j['date']} | {j.get('image', '')}" for j in jeux)
        atomic_write_text(CATALOGUE_FILE, "\n".join(content) + "\n")

def supprimer_jeu(jeu_id):
    jeux = lire_catalogue()
    jeu  = next((j for j in jeux if j["id"] == jeu_id), None)
    if jeu:
        fp = os.path.join(GAMES_FOLDER, jeu["fichier"])
        if os.path.exists(fp): os.remove(fp)
        image_name = jeu.get("image", "")
        image_path = os.path.join(COVERS_FOLDER, os.path.basename(image_name)) if image_name else ""
        if image_path and os.path.isfile(image_path): os.remove(image_path)
        library_path = os.path.join(LIBRARY_FOLDER, str(jeu_id))
        if os.path.isdir(library_path): shutil.rmtree(library_path, ignore_errors=True)
        ecrire_catalogue([j for j in jeux if j["id"] != jeu_id])
        log(f"Jeu supprimé : {jeu['titre']}", "WARN")
        return True
    return False

def format_size(nb):
    if nb < 1024: return f"{nb} o"
    elif nb < 1024*1024: return f"{nb//1024} Ko"
    elif nb < 1024*1024*1024: return f"{nb/(1024*1024):.1f} Mo"
    else: return f"{nb/(1024*1024*1024):.2f} Go"


def system_snapshot():
    total_size = 0
    file_count = 0
    for root, _dirs, files in os.walk(DATA_FOLDER):
        for name in files:
            if name == "cloud.json":
                continue
            try:
                total_size += os.path.getsize(os.path.join(root, name))
                file_count += 1
            except OSError:
                pass
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "pid": os.getpid(),
        "data_dir": DATA_FOLDER,
        "files": file_count,
        "data_size": format_size(total_size),
        "threads": threading.active_count(),
    }


def installed_game_path(game):
    game_id = "".join(c for c in str(game.get("id", "")) if c.isalnum() or c in "_- ").strip()
    filename = os.path.basename(game.get("fichier", ""))
    if not game_id or not filename:
        return "", ""
    folder = os.path.abspath(os.path.join(LIBRARY_FOLDER, game_id))
    target = os.path.abspath(os.path.join(folder, filename))
    if not target.startswith(folder + os.sep):
        return "", ""
    if os.path.isfile(target):
        return folder, target
    legacy = os.path.abspath(os.path.join(LIBRARY_FOLDER, filename))
    if legacy.startswith(os.path.abspath(LIBRARY_FOLDER) + os.sep) and os.path.isfile(legacy):
        return os.path.dirname(legacy), legacy
    return folder, target


def list_installed_games():
    installed = []
    for game in lire_catalogue():
        folder, filepath = installed_game_path(game)
        if filepath and os.path.isfile(filepath):
            item = dict(game)
            item["installed"] = True
            item["installed_size"] = os.path.getsize(filepath)
            installed.append(item)
    return installed


def launch_game_file(filepath, working_directory):
    extension = os.path.splitext(filepath)[1].lower()
    if extension in {".py", ".pyw"}:
        subprocess.Popen([sys.executable, filepath], cwd=working_directory, start_new_session=True)
    elif extension == ".html":
        webbrowser.open_new_tab(Path(filepath).as_uri())
    elif extension == ".app" and sys.platform == "darwin":
        subprocess.Popen(["open", filepath], cwd=working_directory, start_new_session=True)
    elif extension in {".sh", ".command"} and os.name != "nt":
        subprocess.Popen(["/bin/bash", filepath], cwd=working_directory, start_new_session=True)
    elif extension == ".exe" and os.name == "nt":
        os.startfile(filepath, cwd=working_directory)
    elif extension in {".app", ".exe", ".dmg", ".jar"}:
        if sys.platform == "darwin":
            subprocess.Popen(["open", filepath], cwd=working_directory, start_new_session=True)
        elif os.name == "nt":
            os.startfile(filepath, cwd=working_directory)
        else:
            subprocess.Popen(["xdg-open", filepath], cwd=working_directory, start_new_session=True)
    else:
        raise ValueError(f"Format non pris en charge pour le lancement : {extension or 'sans extension'}")


def extract_and_launch_archive(filepath, game_folder):
    extracted = os.path.join(game_folder, "contenu")
    if not os.path.isdir(extracted) or not os.listdir(extracted):
        os.makedirs(extracted, exist_ok=True)
        with zipfile.ZipFile(filepath) as archive:
            root = os.path.realpath(extracted)
            for info in archive.infolist():
                destination = os.path.realpath(os.path.join(extracted, info.filename))
                if destination != root and not destination.startswith(root + os.sep):
                    raise ValueError("Cette archive contient un chemin invalide")
            archive.extractall(extracted)

    candidates = []
    for base, dirs, files in os.walk(extracted):
        app_dirs = [name for name in dirs if name.lower().endswith(".app")]
        candidates.extend(os.path.join(base, name) for name in app_dirs)
        dirs[:] = [name for name in dirs if name != "__MACOSX" and name not in app_dirs]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in {".py", ".pyw", ".html", ".exe", ".sh", ".command"}:
                candidates.append(os.path.join(base, name))
    if not candidates:
        raise ValueError("Aucun lanceur trouvé dans l’archive (formats pris en charge : .app, .py, .html, .exe, .sh)")

    priority = {".app": 0, ".py": 1, ".pyw": 2, ".exe": 3, ".html": 4, ".command": 5, ".sh": 6}
    if os.name == "nt":
        priority = {".exe": 0, ".py": 1, ".pyw": 2, ".html": 3, ".app": 4, ".command": 5, ".sh": 6}
    chosen = min(candidates, key=lambda item: (priority.get(os.path.splitext(item)[1].lower(), 20), len(item), item.lower()))
    launch_game_file(chosen, os.path.dirname(chosen))
    return os.path.relpath(chosen, game_folder)


def run_optimization():
    """Safe local maintenance that can be triggered from the desktop UI."""
    removed_partials = 0
    for root, _dirs, files in os.walk(DATA_FOLDER):
        for name in files:
            if not name.endswith((".part", ".tmp")):
                continue
            path = os.path.join(root, name)
            try:
                os.remove(path)
                removed_partials += 1
            except OSError:
                pass
    gc.collect()
    # Re-read persisted stats so a long-running app never drifts from disk.
    charger_stats()
    return {"success": True, "removed_partials": removed_partials, "system": system_snapshot()}

# ─────────────────────────────────────────
# HANDLER HTTP
# ─────────────────────────────────────────
class GameVaultHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def end_headers(self):
        if not urlparse(self.path).path.startswith("/api"):
            self.send_header("Content-Security-Policy", "default-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def log_message(self, format, *args):
        try:
            code = str(args[1]) if len(args) > 1 else "?"
            req  = str(args[0]) if args else ""
            path = req.split()[1] if " " in req else req
            if not path.startswith("/api"):
                color = C.GREEN if code.startswith("2") else C.RED
                print(f"  {C.GRAY}[{datetime.now().strftime('%H:%M:%S')}]{C.RESET} {C.CYAN}>{C.RESET} {color}{code}{C.RESET}  {C.WHITE}{path}{C.RESET}")
        except Exception:
            pass

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        compressed = "gzip" in self.headers.get("Accept-Encoding", "").lower() and len(body) > 256
        if compressed:
            body = gzip.compress(body, compresslevel=6, mtime=0)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if compressed:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        origin = self.headers.get("Origin", "")
        if origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > MAX_JSON_BODY:
            self.send_json({"success": False, "error": "Requête trop volumineuse"}, 413)
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def require_auth(self, roles=None):
        session = session_from_request(self)
        if not session or (roles and session.get("role") not in roles):
            self.send_json({"success": False, "error": "Authentification requise"}, 401)
            return None
        return session

    def do_OPTIONS(self):
        self.send_response(200)
        origin = self.headers.get("Origin", "")
        if origin in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── GET
    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/api/health":
            self.send_json({"success": True, "app": "GameVault", "version": "4.1.0", "ready": True})

        elif path.startswith("/media/covers/"):
            image_name = os.path.basename(unquote(path[len("/media/covers/"):]))
            image_path = os.path.abspath(os.path.join(COVERS_FOLDER, image_name))
            if not image_name or not image_path.startswith(os.path.abspath(COVERS_FOLDER) + os.sep) or not os.path.isfile(image_path):
                self.send_error(404, "Image introuvable"); return
            content_type = mimetypes.guess_type(image_path)[0] or "application/octet-stream"
            if content_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
                self.send_error(415, "Format d’image non pris en charge"); return
            size = os.path.getsize(image_path)
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            with open(image_path, "rb") as image_file:
                shutil.copyfileobj(image_file, self.wfile, length=CHUNK_SIZE)

        elif path == "/api/roblox/health":
            self.send_json({"success": True, "connected": True, "bridge": "GameVault", "version": "4.1.0"})

        elif path == "/api/roblox/catalogue":
            roblox_games = [
                jeu for jeu in lire_catalogue()
                if "roblox" in jeu.get("genre", "").lower()
                or str(jeu.get("fichier", "")).lower().endswith((".rbxl", ".rbxlx", ".rbxm", ".rbxmx"))
            ]
            self.send_json({"success": True, "games": roblox_games})

        elif path == "/api/system":
            self.send_json({"success": True, "system": system_snapshot()})

        elif path == "/api/cloud/status":
            if not self.require_auth(): return
            try:
                self.send_json({"success": True, "cloud": cloud_sync.public_config(), "manifest": len(cloud_sync.manifest())})
            except CloudSyncError as exc:
                self.send_json({"success": False, "error": str(exc)}, 500)

        elif path == "/api/comptes":
            if not self.require_auth({"admin"}): return
            comptes = lire_comptes()
            safe = [{k: v for k, v in c.items() if k != "password"} for c in comptes]
            self.send_json({"success": True, "comptes": safe})

        elif path.startswith("/api/delete-compte/") or path.startswith("/api/delete/"):
            if not self.require_auth({"admin"}): return
            account_id = path.split("/api/delete-compte/")[-1] if "/api/delete-compte/" in path else path.split("/api/delete/")[-1]
            supprimer_compte(account_id)
            self.send_json({"success": True})

        elif path == "/api/catalogue":
            self.send_json({"success": True, "jeux": lire_catalogue()})

        elif path == "/api/library":
            if not self.require_auth(): return
            self.send_json({"success": True, "games": list_installed_games()})

        elif path.startswith("/api/delete-jeu/"):
            ok = supprimer_jeu(path.split("/api/delete-jeu/")[1])
            self.send_json({"success": ok})

        elif path == "/api/stats":
            jeux = lire_catalogue()
            total_dl = sum(download_stats.values())
            total_size = sum(
                os.path.getsize(os.path.join(GAMES_FOLDER, j["fichier"]))
                for j in jeux if os.path.exists(os.path.join(GAMES_FOLDER, j["fichier"]))
            )
            self.send_json({
                "success": True,
                "total_jeux": len(jeux),
                "total_telechargements": total_dl,
                "total_size": format_size(total_size),
                "jeux_stats": [{"titre": j["titre"], "telechargements": j["telechargements"]} for j in jeux]
            })

        elif path.startswith("/api/download/"):
            # ── Téléchargement avec support Range (pause/reprise)
            jeu_id = path.split("/api/download/")[1]
            jeux   = lire_catalogue()
            jeu    = next((j for j in jeux if j["id"] == jeu_id), None)
            if not jeu:
                self.send_json({"error": "Jeu introuvable"}, 404); return

            filepath = os.path.abspath(os.path.join(GAMES_FOLDER, jeu["fichier"]))
            if not filepath.startswith(os.path.abspath(GAMES_FOLDER) + os.sep):
                self.send_json({"error": "Nom de fichier invalide"}, 400); return
            if not os.path.exists(filepath):
                self.send_json({"error": "Fichier manquant sur le serveur"}, 404); return

            file_size = os.path.getsize(filepath)
            range_header = self.headers.get("Range")

            if range_header:
                # Téléchargement partiel (reprise)
                try:
                    range_val = range_header.replace("bytes=", "")
                    start_str, end_str = range_val.split("-")
                    start = int(start_str)
                    end   = int(end_str) if end_str else file_size - 1
                    end   = min(end, file_size - 1)
                    if start < 0 or start >= file_size or end < start:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{file_size}")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    length = end - start + 1

                    self.send_response(206)  # Partial Content
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                    self.send_header("Content-Length", str(length))
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Content-Disposition", f'attachment; filename="{jeu["fichier"]}"')
                    self.end_headers()

                    with open(filepath, "rb") as f:
                        f.seek(start)
                        remaining = length
                        while remaining > 0:
                            chunk = f.read(min(CHUNK_SIZE, remaining))
                            if not chunk: break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)

                    log(f"Reprise DL : {jeu['titre']} [{start}-{end}]", "DL")

                except Exception as e:
                    log(f"Erreur Range : {e}", "ERR")
            else:
                # Téléchargement complet
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(file_size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Disposition", f'attachment; filename="{jeu["fichier"]}"')
                self.end_headers()

                with open(filepath, "rb") as f:
                    while True:
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk: break
                        self.wfile.write(chunk)

                incr_stat(jeu_id)
                log(f"Téléchargement : {jeu['titre']} ({jeu['taille']})", "DL")

        else:
            super().do_GET()

    # ── POST
    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/login":
            data = self.read_body()
            compte, err = connexion(data.get("email","").strip(), data.get("password","").strip())
            if err:
                self.send_json({"success": False, "error": err}, 401)
            else:
                safe = {k: v for k, v in compte.items() if k != "password"}
                self.send_json({"success": True, "user": safe, "token": create_session(compte)})

        elif path == "/api/logout":
            header = self.headers.get("Authorization", "")
            if header.startswith("Bearer "):
                active_sessions.pop(header[7:].strip(), None)
            self.send_json({"success": True})

        elif path == "/api/register":
            data = self.read_body()
            name, email, pw = data.get("name","").strip(), data.get("email","").strip(), data.get("password","").strip()
            if not name or not email or not pw:
                self.send_json({"success": False, "error": "Champs manquants"}, 400); return
            if len(pw) < 6:
                self.send_json({"success": False, "error": "Mot de passe trop court"}, 400); return
            compte, err = ajouter_compte(name, email, pw)
            if err:
                self.send_json({"success": False, "error": err}, 409)
            else:
                safe = {k: v for k, v in compte.items() if k != "password"}
                self.send_json({"success": True, "user": safe, "token": create_session(compte)})

        elif path == "/api/update":
            data = self.read_body()
            session = self.require_auth()
            if not session: return
            if str(data.get("id", "")) != str(session.get("id")) and session.get("role") != "admin":
                self.send_json({"success": False, "error": "Action non autorisée"}, 403); return
            compte, err = modifier_compte(data.get("id"), name=data.get("name"),
                                          email=data.get("email"), password=data.get("password"))
            if err:
                self.send_json({"success": False, "error": err}, 404)
            else:
                safe = {k: v for k, v in compte.items() if k != "password"}
                self.send_json({"success": True, "user": safe})

        elif path == "/api/delete-compte":
            data = self.read_body()
            if not self.require_auth({"admin"}): return
            supprimer_compte(str(data.get("id", "")))
            self.send_json({"success": True})

        elif path == "/api/delete-jeu":
            data = self.read_body()
            if not self.require_auth({"admin"}): return
            ok = supprimer_jeu(str(data.get("id", "")))
            self.send_json({"success": ok}, 200 if ok else 404)

        elif path == "/api/library/install":
            if not self.require_auth(): return
            data = self.read_body()
            game_id = str(data.get("id", ""))
            game = next((item for item in lire_catalogue() if item["id"] == game_id), None)
            if not game:
                self.send_json({"success": False, "error": "Jeu introuvable"}, 404); return
            source = os.path.abspath(os.path.join(GAMES_FOLDER, os.path.basename(game["fichier"])))
            if not source.startswith(os.path.abspath(GAMES_FOLDER) + os.sep) or not os.path.isfile(source):
                self.send_json({"success": False, "error": "Fichier source introuvable"}, 404); return
            folder = os.path.abspath(os.path.join(LIBRARY_FOLDER, game_id))
            if not folder.startswith(os.path.abspath(LIBRARY_FOLDER) + os.sep):
                self.send_json({"success": False, "error": "Identifiant invalide"}, 400); return
            destination = os.path.join(folder, os.path.basename(game["fichier"]))
            if os.path.isfile(destination) or os.path.isfile(os.path.join(LIBRARY_FOLDER, os.path.basename(game["fichier"]))):
                self.send_json({"success": True, "already_installed": True, "game": game}); return
            os.makedirs(folder, exist_ok=True)
            temporary = destination + ".part"
            try:
                with open(source, "rb") as source_file, open(temporary, "wb") as destination_file:
                    shutil.copyfileobj(source_file, destination_file, length=CHUNK_SIZE)
                    destination_file.flush()
                    os.fsync(destination_file.fileno())
                os.replace(temporary, destination)
            except OSError as exc:
                try: os.unlink(temporary)
                except OSError: pass
                self.send_json({"success": False, "error": f"Installation impossible : {exc}"}, 507); return
            log(f"Jeu installé : {game['titre']} ({format_size(os.path.getsize(destination))})", "OK")
            self.send_json({"success": True, "already_installed": False, "game": game})

        elif path == "/api/library/launch":
            if not self.require_auth(): return
            data = self.read_body()
            game_id = str(data.get("id", ""))
            game = next((item for item in lire_catalogue() if item["id"] == game_id), None)
            if not game:
                self.send_json({"success": False, "error": "Jeu introuvable"}, 404); return
            folder, filepath = installed_game_path(game)
            if not filepath or not os.path.isfile(filepath):
                self.send_json({"success": False, "error": "Installe ce jeu dans ta bibliothèque avant de le lancer."}, 409); return
            try:
                if os.path.splitext(filepath)[1].lower() == ".zip":
                    entrypoint = extract_and_launch_archive(filepath, folder)
                else:
                    launch_game_file(filepath, folder)
                    entrypoint = os.path.basename(filepath)
                log(f"Jeu lancé : {game['titre']} ({entrypoint})", "OK")
                self.send_json({"success": True, "entrypoint": entrypoint})
            except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
                self.send_json({"success": False, "error": f"Lancement impossible : {exc}"}, 400)

        elif path == "/api/cloud/configure":
            data = self.read_body()
            if not self.require_auth(): return
            try:
                current = cloud_sync.load_config()
                # An empty password means "keep the current credential" when
                # editing an existing endpoint from the settings screen.
                password = data.get("password") or current.get("password", "")
                config = cloud_sync.save_config({
                    "enabled": bool(data.get("enabled", True)),
                    "url": data.get("url", ""),
                    "username": data.get("username", ""),
                    "password": password,
                    "remote_path": data.get("remote_path", "GameVault"),
                    "last_sync": current.get("last_sync"),
                })
                config.pop("password", None)
                self.send_json({"success": True, "cloud": config})
            except CloudSyncError as exc:
                self.send_json({"success": False, "error": str(exc)}, 400)

        elif path == "/api/cloud/sync":
            data = self.read_body()
            if not self.require_auth(): return
            try:
                result = cloud_sync.sync(data.get("direction", "two-way"))
                self.send_json(result)
            except CloudSyncError as exc:
                self.send_json({"success": False, "error": str(exc)}, 502)

        elif path == "/api/optimization":
            if not self.require_auth(): return
            self.send_json(run_optimization())

        elif path == "/api/upload-jeu":
            if not self.require_auth({"admin"}): return
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.send_json({"success": False, "error": "Multipart requis"}, 400); return
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0:
                self.send_json({"success": False, "error": "Fichier vide"}, 400); return
            if MAX_UPLOAD_BYTES and length > MAX_UPLOAD_BYTES:
                self.send_json({"success": False, "error": f"Upload supérieur à la limite configurée ({MAX_UPLOAD_BYTES // (1024 * 1024)} Mo)"}, 413); return

            token = secrets.token_hex(10)
            game_temp = os.path.join(GAMES_FOLDER, f".upload-{token}.part")
            cover_temp = os.path.join(COVERS_FOLDER, f".upload-{token}.part")
            try:
                fields, uploads = read_multipart(self.rfile, content_type, length, game_temp, cover_temp)
                game_upload = uploads.get("game")
                if not game_upload or game_upload["size"] <= 0:
                    raise ValueError("Fichier du jeu manquant ou vide")
                filename = game_upload["filename"]
                safe_name = "".join(c for c in os.path.basename(filename) if c.isalnum() or c in "._- ").strip()
                if not safe_name or safe_name in {".", ".."}:
                    raise ValueError("Nom de fichier du jeu invalide")
                filepath = os.path.join(GAMES_FOLDER, safe_name)
                if os.path.exists(filepath):
                    stem, extension = os.path.splitext(safe_name)
                    safe_name = f"{stem}-{int(time.time() * 1000)}{extension}"
                    filepath = os.path.join(GAMES_FOLDER, safe_name)

                image_name = ""
                cover_upload = uploads.get("image")
                if cover_upload and cover_upload["size"]:
                    cover_extension = os.path.splitext(cover_upload["filename"])[1].lower()
                    if cover_extension not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                        raise ValueError("Image acceptée : PNG, JPG, WEBP ou GIF")
                    with open(cover_temp, "rb") as cover_file:
                        signature = cover_file.read(16)
                    valid_signatures = {
                        ".png": signature.startswith(b"\x89PNG\r\n\x1a\n"),
                        ".jpg": signature.startswith(b"\xff\xd8\xff"),
                        ".jpeg": signature.startswith(b"\xff\xd8\xff"),
                        ".webp": signature.startswith(b"RIFF") and signature[8:12] == b"WEBP",
                        ".gif": signature.startswith((b"GIF87a", b"GIF89a")),
                    }
                    if not valid_signatures[cover_extension]:
                        raise ValueError("Le contenu de l’image ne correspond pas à son extension")
                    image_name = f"{token}{cover_extension}"
                    os.replace(cover_temp, os.path.join(COVERS_FOLDER, image_name))

                os.replace(game_temp, filepath)
            except OverflowError as exc:
                for temporary in (game_temp, cover_temp):
                    try: os.unlink(temporary)
                    except OSError: pass
                self.send_json({"success": False, "error": str(exc)}, 413); return
            except (OSError, ValueError, UnicodeError) as exc:
                for temporary in (game_temp, cover_temp):
                    try: os.unlink(temporary)
                    except OSError: pass
                self.send_json({"success": False, "error": str(exc)}, 400); return

            taille_str = format_size(game_upload["size"])
            jeux = lire_catalogue()
            jeu = {
                "id":          str(int(time.time() * 1000)),
                "titre":       flat_text(fields.get("titre", safe_name), 160),
                "genre":       flat_text(fields.get("genre", "Jeu"), 80),
                "description": flat_text(fields.get("description", "Pas de description."), 1000),
                "fichier":     safe_name,
                "taille":      taille_str,
                "emoji":       flat_text(fields.get("emoji", "🎮"), 12),
                "image":       image_name,
                "date":        datetime.now().strftime("%d/%m/%Y"),
                "telechargements": 0
            }
            jeux.append(jeu)
            ecrire_catalogue(jeux)
            log(f"Jeu uploadé : {jeu['titre']} ({taille_str})", "UPLOAD")
            self.send_json({"success": True, "jeu": jeu})

        else:
            self.send_json({"error": "Route inconnue"}, 404)

# ─────────────────────────────────────────
# LANCEMENT
# ─────────────────────────────────────────
def main():
    os.system('color')
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"""
{C.CYAN}+================================================+
|  {C.BOLD}{C.WHITE}GAMEVAULT - SERVEUR v4{C.RESET}{C.CYAN}                       |
|  {C.GRAY}Cloud WebDAV + téléchargements avec reprise{C.CYAN} |
+================================================+{C.RESET}
""")
    init_dossiers()
    os.chdir(SITE_FOLDER)
    log(f"Dossier  : {SITE_FOLDER}", "OK")
    log(f"Adresse  : {HOST}:{PORT}", "OK")

    print(f"""
{C.CYAN}  Pages :
  {C.WHITE}http://localhost:{PORT}/store.html{C.CYAN}     -> Boutique
  {C.WHITE}http://localhost:{PORT}/accounts.html{C.CYAN}  -> Comptes
  {C.YELLOW}CTRL+C pour arreter{C.RESET}
""")

    if os.environ.get("GAMEVAULT_NO_BROWSER") != "1":
        def open_browser():
            time.sleep(1)
            webbrowser.open(f"http://localhost:{PORT}/store.html")

        threading.Thread(target=open_browser, daemon=True).start()
    print(f"  {C.GRAY}Données : {DATA_FOLDER}{C.RESET}")
    print(f"  {C.GRAY}Logs :{C.RESET}\n")

    try:
        class ReusableThreadingServer(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        with ReusableThreadingServer((HOST, PORT), GameVaultHandler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print(f"\n  {C.YELLOW}Serveur arrete.{C.RESET}\n")
    except OSError as exc:
        log(f"Impossible d'ouvrir {HOST}:{PORT} : {exc}", "ERR")
        raise

if __name__ == "__main__":
    main()
