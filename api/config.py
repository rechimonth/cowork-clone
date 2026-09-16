"""Configuración y validación de seguridad de la API.

La API expone el agente por HTTP, así que su postura por defecto es cerrada:
sin token configurado el servidor **no arranca**, y solo se aceptan directorios
raíz que pasen la validación de :func:`validate_root_dir`. Ambas cosas son
deliberadas: un agente que renombra archivos no debe quedar accesible por
accidente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Tiempo máximo que el worker espera la decisión humana antes de abortar. Es
# más laxo que el timeout de terminal (60s) porque aquí el humano puede estar
# mirando el plan en el frontend, no delante de un prompt.
DEFAULT_APPROVAL_TIMEOUT_S = 300

# Orígenes permitidos por defecto: el dev server de Vite y el webview de Tauri.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:1420",
    "http://127.0.0.1:1420",
    "tauri://localhost",
    "https://tauri.localhost",
)

# Rutas del sistema que nunca deben ser raíz del agente. Aunque las operaciones
# estén limitadas a `mkdir`/`rename` dentro del root, apuntar el agente a `/etc`
# o `/usr` es un error de configuración con consecuencias difíciles de revertir.
# Se rechaza tanto la ruta exacta como cualquier cosa *dentro* de ella, porque un
# subdirectorio del sistema es igual de peligroso y porque en muchas distros
# `/bin` o `/lib` son symlinks a `/usr/bin` y `/usr/lib`: comparar solo la
# igualdad dejaría pasar `/bin` resuelto a `/usr/bin`.
SYSTEM_ROOTS = (
    "/etc",
    "/usr",
    "/bin",
    "/sbin",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/lib",
    "/lib64",
    "/var",
    "/opt",
    "/srv",
    "/run",
    "/root",
    "/System",
    "/Library",
    "/Windows",
)

# Subdirectorios sensibles dentro del home. Se rechazan como raíz porque el
# agente lee previews de los archivos que escanea; un root que contiene claves
# privadas filtraría su contenido al LLM y al audit log.
SENSITIVE_HOME_SUBDIRS = (".ssh", ".aws", ".gnupg", ".kube", ".config", ".docker")


class ConfigurationError(RuntimeError):
    """La configuración dejaría la API insegura o inoperante."""


def _split_env(name: str) -> tuple[str, ...]:
    """Lee una lista separada por comas, ignorando vacíos."""
    raw = os.getenv(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _is_sensitive(path: Path) -> str | None:
    """Devuelve el motivo del rechazo si ``path`` es una ruta sensible.

    ``path`` debe venir ya resuelto. La comparación es por jerarquía (¿está
    ``path`` dentro de un root del sistema?) y no por igualdad, de modo que
    ``/etc/nginx`` o ``/usr/local`` también se rechazan, y que un symlink como
    ``/bin -> /usr/bin`` no pueda esquivar la denylist.
    """
    resolved = path.resolve()

    # El filesystem root se trata aparte porque ningún otro path puede tenerlo
    # como descendiente: su comprobación es la igualdad, no la contención.
    if resolved == resolved.parent:
        return f"{resolved} es la raíz del filesystem"

    for system_root in SYSTEM_ROOTS:
        root = Path(system_root)
        if resolved == root or root in resolved.parents:
            return f"{resolved} está dentro de {root} (ruta del sistema)"

    home = Path.home().resolve()
    if resolved == home:
        return "el home del usuario no puede ser la raíz del agente"

    for sub in SENSITIVE_HOME_SUBDIRS:
        sensitive = home / sub
        if resolved == sensitive or sensitive in resolved.parents:
            return f"{resolved} está dentro de {sensitive} (contiene credenciales)"

    return None


def validate_root_dir(root_dir: str | Path, allowed_roots: tuple[Path, ...] = ()) -> Path:
    """Valida que ``root_dir`` sea un directorio apto para operar el agente.

    Aplica tres barreras: existencia, denylist de rutas sensibles y, si hay
    ``allowed_roots`` configurados, contención dentro de uno de ellos. La
    contención se comprueba sobre la ruta *resuelta* y por jerarquía de
    ``pathlib`` en lugar de prefijo textual, igual que hace el ejecutor.
    """
    raw = Path(root_dir).expanduser()
    if not raw.is_absolute():
        raise ConfigurationError(f"root_dir debe ser una ruta absoluta: {root_dir}")

    resolved = raw.resolve()

    if not resolved.exists():
        raise ConfigurationError(f"root_dir no existe: {resolved}")
    if not resolved.is_dir():
        raise ConfigurationError(f"root_dir no es un directorio: {resolved}")

    reason = _is_sensitive(resolved)
    if reason is not None:
        raise ConfigurationError(f"root_dir rechazado: {reason}")

    if allowed_roots:
        roots = tuple(r.resolve() for r in allowed_roots)
        if not any(r == resolved or r in resolved.parents for r in roots):
            raise ConfigurationError(
                f"root_dir fuera de COWORK_ALLOWED_ROOTS: {resolved}. "
                f"Permitidos: {', '.join(str(r) for r in roots)}"
            )

    return resolved


@dataclass(frozen=True)
class ApiConfig:
    """Configuración inmutable de la API, leída del entorno."""

    token: str | None
    allowed_roots: tuple[Path, ...]
    cors_origins: tuple[str, ...]
    state_dir: Path
    approval_timeout_s: int
    allow_insecure: bool
    max_sessions: int

    @classmethod
    def from_env(cls) -> ApiConfig:
        state_dir = Path(os.getenv("COWORK_STATE_DIR", "./api-state")).expanduser()
        return cls(
            token=os.getenv("COWORK_API_TOKEN") or None,
            allowed_roots=tuple(Path(p).expanduser() for p in _split_env("COWORK_ALLOWED_ROOTS")),
            cors_origins=_split_env("COWORK_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS,
            state_dir=state_dir,
            approval_timeout_s=int(
                os.getenv("COWORK_APPROVAL_TIMEOUT_S", str(DEFAULT_APPROVAL_TIMEOUT_S))
            ),
            allow_insecure=_bool_env("COWORK_ALLOW_INSECURE"),
            max_sessions=int(os.getenv("COWORK_MAX_SESSIONS", "50")),
        )

    def validate(self) -> None:
        """Falla al arrancar si la API quedaría abierta o mal dimensionada.

        El caso importante es el token: sin él cualquiera que alcance el puerto
        podría aprobar un plan y renombrar archivos. Se exige un opt-in
        explícito (`COWORK_ALLOW_INSECURE=true`) para correr sin autenticación.
        """
        if not self.token and not self.allow_insecure:
            raise ConfigurationError(
                "Falta COWORK_API_TOKEN. Definí un token o, para desarrollo local "
                "consciente, exportá COWORK_ALLOW_INSECURE=true"
            )
        if self.token is not None and len(self.token) < 16:
            raise ConfigurationError(
                "COWORK_API_TOKEN es demasiado corto (<16 caracteres); usá un secreto real"
            )
        if self.approval_timeout_s <= 0:
            raise ConfigurationError("COWORK_APPROVAL_TIMEOUT_S debe ser positivo")
        if self.max_sessions <= 0:
            raise ConfigurationError("COWORK_MAX_SESSIONS debe ser positivo")

    @property
    def authentication_required(self) -> bool:
        return self.token is not None
