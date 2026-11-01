"""WebDAV synchronisation helpers for GameVault.

The cloud layer is deliberately provider agnostic: any WebDAV-compatible
endpoint (Nextcloud, ownCloud, Koofr, Infomaniak, etc.) can be used without
shipping provider-specific SDKs in the desktop app.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


class CloudSyncError(RuntimeError):
    """Raised when a cloud operation cannot be completed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WebDAVSync:
    """Small, dependency-free WebDAV client used by the local API."""

    def __init__(self, data_root: str | Path, config_path: str | Path):
        self.data_root = Path(data_root)
        self.config_path = Path(config_path)

    def load_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {"enabled": False, "url": "", "username": "", "remote_path": "GameVault"}
        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                config = json.load(handle)
            return {
                "enabled": bool(config.get("enabled", False)),
                "url": str(config.get("url", "")).strip(),
                "username": str(config.get("username", "")).strip(),
                "password": str(config.get("password", "")),
                "remote_path": str(config.get("remote_path", "GameVault")).strip("/") or "GameVault",
                "last_sync": config.get("last_sync"),
            }
        except (OSError, ValueError) as exc:
            raise CloudSyncError(f"Configuration cloud illisible : {exc}") from exc

    def save_config(self, config: dict[str, Any]) -> dict[str, Any]:
        url = str(config.get("url", "")).strip().rstrip("/")
        if url and not url.startswith(("https://", "http://")):
            raise CloudSyncError("L’URL cloud doit commencer par https:// ou http://")
        safe = {
            "enabled": bool(config.get("enabled", False)),
            "url": url,
            "username": str(config.get("username", "")).strip(),
            "password": str(config.get("password", "")),
            "remote_path": str(config.get("remote_path", "GameVault")).strip("/") or "GameVault",
            "last_sync": config.get("last_sync"),
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="cloud-", suffix=".json", dir=self.config_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(safe, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.config_path)
            try:
                os.chmod(self.config_path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        return safe

    def public_config(self) -> dict[str, Any]:
        config = self.load_config()
        config.pop("password", None)
        config["configured"] = bool(config.get("url"))
        return config

    def _base_url(self, config: dict[str, Any]) -> str:
        url = config.get("url", "").rstrip("/")
        remote_path = str(config.get("remote_path", "GameVault")).strip("/")
        if not url:
            raise CloudSyncError("Aucun endpoint cloud n’est configuré")
        return f"{url}/{remote_path}" if remote_path else url

    def _request(self, method: str, url: str, body: bytes | None = None) -> bytes:
        config = self.load_config()
        headers = {"User-Agent": "GameVault/4.1", "Accept": "application/json, */*"}
        if body is not None:
            headers["Content-Type"] = "application/json" if url.endswith("/manifest.json") else "application/octet-stream"
        username = config.get("username", "")
        password = config.get("password", "")
        if username:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405) and method == "MKCOL":
                return b""
            detail = exc.read(300).decode("utf-8", errors="replace")
            raise CloudSyncError(f"Cloud HTTP {exc.code}: {detail or exc.reason}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CloudSyncError(f"Connexion cloud impossible : {exc}") from exc

    def _remote_url(self, base: str, relative: str) -> str:
        parts = [p for p in relative.replace("\\", "/").split("/") if p]
        return base.rstrip("/") + "/" + "/".join(urllib.parse.quote(p) for p in parts)

    @staticmethod
    def _safe_relative(relative: str) -> bool:
        path = PurePosixPath(str(relative))
        return bool(relative) and str(path) not in {"", "."} and not path.is_absolute() and ".." not in path.parts and "cloud.json" not in path.parts

    def _ensure_remote_dirs(self, base: str, relative: str) -> None:
        # WebDAV servers create parents one by one. Plain HTTP endpoints can
        # simply ignore MKCOL and still support PUT on a pre-created folder.
        try:
            self._request("MKCOL", base)
        except CloudSyncError:
            pass
        segments = [p for p in relative.replace("\\", "/").split("/") if p]
        current = base.rstrip("/")
        for segment in segments[:-1]:
            current += "/" + urllib.parse.quote(segment)
            try:
                self._request("MKCOL", current)
            except CloudSyncError:
                pass

    def manifest(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        if not self.data_root.exists():
            return result
        for path in self.data_root.rglob("*"):
            if not path.is_file() or path.name == "cloud.json" or path.name.endswith((".part", ".tmp")):
                continue
            relative = path.relative_to(self.data_root).as_posix()
            if relative.startswith("backups/"):
                continue
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            stat = path.stat()
            result[relative] = {
                "sha256": digest.hexdigest(),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        return result

    def _create_backup(self) -> str | None:
        files = [path for path in self.data_root.rglob("*") if path.is_file() and "backups" not in path.relative_to(self.data_root).parts and path.name != "cloud.json"]
        if not files:
            return None
        backup_dir = self.data_root / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        filename = backup_dir / f"pre-sync-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
        with zipfile.ZipFile(filename, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for path in files:
                archive.write(path, path.relative_to(self.data_root).as_posix())
        backups = sorted(backup_dir.glob("pre-sync-*.zip"), key=lambda item: item.stat().st_mtime, reverse=True)
        for stale in backups[5:]:
            try: stale.unlink()
            except OSError: pass
        return str(filename)

    def _download_file(self, base: str, relative: str, destination: Path, mtime: float | None = None) -> None:
        payload = self._request("GET", self._remote_url(base, relative))
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="gv-", suffix=".part", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
            if mtime is not None:
                os.utime(destination, (float(mtime), float(mtime)))
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def sync(self, direction: str = "two-way") -> dict[str, Any]:
        if direction not in {"push", "pull", "two-way"}:
            raise CloudSyncError("Direction de synchronisation invalide")
        config = self.load_config()
        if not config.get("enabled") or not config.get("url"):
            raise CloudSyncError("Activez et configurez le cloud avant de synchroniser")

        base = self._base_url(config)
        local = self.manifest()
        backup = self._create_backup()
        remote_manifest_url = self._remote_url(base, "manifest.json")
        remote: dict[str, dict[str, Any]] = {}
        try:
            remote_data = self._request("GET", remote_manifest_url)
            remote = json.loads(remote_data.decode("utf-8"))
            if not isinstance(remote, dict):
                raise CloudSyncError("Manifest cloud invalide")
            remote = {str(key): value for key, value in remote.items() if self._safe_relative(str(key)) and isinstance(value, dict)}
        except CloudSyncError as exc:
            # A first sync has no manifest yet; other failures remain visible.
            if "HTTP 404" not in str(exc):
                raise

        uploaded, downloaded, conflicts = [], [], []
        all_paths = sorted(set(local) | set(remote))
        for relative in all_paths:
            local_item = local.get(relative)
            remote_item = remote.get(relative)
            try:
                remote_mtime = float(remote_item.get("mtime", 0)) if remote_item else 0.0
            except (TypeError, ValueError):
                remote_mtime = 0.0
            if direction == "push":
                should_upload = local_item is not None and local_item != remote_item
                should_download = False
            elif direction == "pull":
                should_upload = False
                should_download = remote_item is not None and local_item != remote_item
            else:
                should_upload = local_item is not None and remote_item is None
                should_download = remote_item is not None and local_item is None
                if local_item and remote_item and local_item != remote_item:
                    # Newer mtime wins. If timestamps are equal, keep local and
                    # report a conflict rather than silently overwriting data.
                    if remote_mtime > float(local_item.get("mtime", 0)):
                        should_download = True
                    elif remote_mtime == float(local_item.get("mtime", 0)):
                        conflicts.append(relative)
                    else:
                        should_upload = True
            if should_upload:
                source = self.data_root / relative
                self._ensure_remote_dirs(base, relative)
                self._request("PUT", self._remote_url(base, relative), source.read_bytes())
                uploaded.append(relative)
            elif should_download:
                self._download_file(base, relative, self.data_root / relative, remote_mtime if remote_item else None)
                downloaded.append(relative)

        final_manifest = self.manifest()
        self._request("PUT", remote_manifest_url, json.dumps(final_manifest, ensure_ascii=False).encode("utf-8"))
        config["last_sync"] = utc_now()
        self.save_config(config)
        return {
            "success": True,
            "direction": direction,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "conflicts": conflicts,
            "files": len(final_manifest),
            "backup": backup,
            "last_sync": config["last_sync"],
        }
