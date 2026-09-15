from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Protocol
from urllib.parse import urlparse

import requests
from pydantic import BaseModel, ValidationError

from audit_logger import AuditLogger
from models import PlannerInput, PlannerOutput, ExecutionPlan, RenameAction, CreateDirAction


_LOGGER = logging.getLogger(__name__)


MODEL_NAME = "qwen2.5:3b-instruct"
OLLAMA_ENDPOINT = "http://localhost:11434/api/generate"

# Hosts para los que HTTP en texto plano es aceptable (tráfico local).
LOCALHOST_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}

# Timeout (segundos) de cada request HTTP al LLM. Un valor alto es tolerable
# porque el HITL ocurre después; si el modelo no responde, se cae al fallback.
DEFAULT_REQUEST_TIMEOUT_S = 60

# Cantidad de intentos totales ante errores transitorios del proveedor LLM.
DEFAULT_RETRIES = 3

# Longitud máxima de un filename sugerido por el LLM (límite típico de
# filesystem + margen para nombres largos generados por el modelo).
MAX_FILENAME_LENGTH = 120

# Truncado del preview que se envía dentro del prompt al LLM. Acota el costo
# de tokens por archivo.
PROMPT_PREVIEW_MAX_CHARS = 3000

# Truncado del preview incluido en el prompt del planner (varios archivos a la
# vez), por lo que es bastante más agresivo que PROMPT_PREVIEW_MAX_CHARS.
PLANNER_PROMPT_PREVIEW_MAX_CHARS = 300


class FileAnalysis(BaseModel):
    category: str
    suggested_name: str
    reason: str

    def __getitem__(self, key: str) -> str:
        return getattr(self, key)


class OllamaProviderError(RuntimeError):
    pass


INVALID_FILENAME_CHARS = '/\\:*?"<>|'
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


def safe_filename(value: str, max_length: int = MAX_FILENAME_LENGTH) -> str:
    raw = str(value or "")
    cleaned = raw.translate({ord(ch): None for ch in INVALID_FILENAME_CHARS})
    cleaned = re.sub(r"[\x00-\x1f]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")

    if not cleaned:
        cleaned = "untitled"

    stem, ext = os.path.splitext(cleaned)
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
        stem, ext = os.path.splitext(cleaned)

    if len(cleaned) > max_length:
        if ext and len(ext) < max_length:
            keep = max_length - len(ext)
            cleaned = f"{stem[:keep].rstrip('. ')}{ext}"
        else:
            cleaned = cleaned[:max_length].rstrip(". ")

    return cleaned or "untitled"


def fallback_file_analysis(original_filename: str) -> FileAnalysis:
    try:
        filename = str(original_filename or "unknown")
    except Exception:
        filename = "unknown"
    return FileAnalysis(category="unknown", suggested_name=filename, reason="fallback")


def _pydantic_validate_file_analysis(data: dict[str, Any]) -> FileAnalysis:
    return FileAnalysis.model_validate(data)


def parse_file_analysis_json(raw_text: str, *, fallback_to: str | None = None) -> FileAnalysis:
    """Parsea la respuesta cruda del LLM a un ``FileAnalysis``.

    Es retrocompatible: para una respuesta válida devuelve el mismo resultado que
    antes. Maneja explícitamente ``json.JSONDecodeError`` (y fallos de validación
    Pydantic) y, si se pasa ``fallback_to``, devuelve un análisis de fallback en
    lugar de propagar la excepción. ``safe_filename`` se aplica siempre como red
    de seguridad para que el nombre sugerido nunca sea peligroso, incluso si el
    JSON traía un nombre inválido.
    """
    try:
        analysis = None
        for data in _iter_json_objects(raw_text):
            try:
                analysis = _pydantic_validate_file_analysis(data)
                break
            except (ValidationError, ValueError, TypeError):
                # El objeto no cumple el schema; probamos con el siguiente.
                continue
        if analysis is None:
            raise ValueError("No valid FileAnalysis JSON object found")
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError):
        if fallback_to is None:
            raise
        analysis = fallback_file_analysis(fallback_to)

    safe_name = safe_filename(analysis.suggested_name)
    if not safe_name:
        safe_name = safe_filename(fallback_to or "untitled")
    analysis.suggested_name = safe_name
    return analysis


def _unwrap_response_object(parsed: Any) -> dict[str, Any] | None:
    """Desenvuelve el wrapper de Ollama ``{"response": ...}`` si está presente."""
    if not isinstance(parsed, dict):
        return None
    model_response = parsed.get("response")
    if isinstance(model_response, str):
        return _parse_json_object(model_response)
    if isinstance(model_response, dict):
        return model_response
    return parsed


def _iter_brace_candidates(text: str):
    """Genera substrings que comienzan en cada ``{`` y cierran balanceadamente.

    Soporta texto antes y después del objeto JSON y respeta las comillas para no
    confundir llaves que aparecen dentro de un string.
    """
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        depth = 0
        quote: str | None = None
        escaped = False
        for i in range(start, len(text)):
            current = text[i]
            if escaped:
                escaped = False
                continue
            if current == "\\":
                escaped = True
                continue
            if quote:
                if current == quote:
                    quote = None
                continue
            if current in ("'", '"'):
                quote = current
                continue
            if current == "{":
                depth += 1
            elif current == "}":
                depth -= 1
                if depth == 0:
                    yield text[start : i + 1]
                    break


def _normalize_json_like(candidate: str) -> str:
    """Normaliza JSON "casi válido": comillas simples y literales de Python.

    Se usa SOLO como último recurso cuando ``json.loads`` falla. No pretende ser
    un parser general: cubre el caso común de modelos que devuelven comillas
    simples en lugar de dobles.
    """
    normalized = candidate.replace("'", '"')
    for py_literal, json_literal in (("True", "true"), ("False", "false"), ("None", "null")):
        normalized = re.sub(rf"\b{py_literal}\b", json_literal, normalized)
    return normalized


def _try_parse_candidate(candidate: str) -> dict[str, Any] | None:
    """Intenta parsear un candidato a dict probando JSON estricto y laxitud."""
    for text in (candidate, _normalize_json_like(candidate)):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            unwrapped = _unwrap_response_object(parsed)
            if unwrapped is not None:
                return unwrapped
    return None


def _normalize_fences(text: str) -> str:
    """Quita fences de markdown (con o sin cierre) y devuelve el contenido."""
    fenced = re.search(
        r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE
    )
    if fenced:
        return fenced.group(1).strip()
    # Fence abierto (sin cierre) o el modelo cortó la respuesta.
    opening = re.search(r"```(?:json)?\s*", text, flags=re.IGNORECASE)
    if opening:
        return text[opening.end() :].strip()
    return text


def _iter_json_objects(raw_text: str):
    """Genera los objetos JSON parseables de una respuesta de LLM, en orden.

    Tolera texto antes/después del bloque, fences de markdown (incluso sin
    cierre) y comillas simples. Se usa en ``parse_file_analysis_json`` para
    poder descartar objetos que no cumplen el schema y seguir buscando.
    """
    text = str(raw_text or "").strip()
    if not text:
        return
    text = _normalize_fences(text)

    # Intento directo sobre el texto completo.
    parsed = _try_parse_candidate(text)
    if parsed is not None:
        yield parsed

    # Texto extra alrededor del bloque: llaves balanceadas.
    for candidate in _iter_brace_candidates(text):
        parsed = _try_parse_candidate(candidate)
        if parsed is not None:
            yield parsed


def _parse_json_object(raw_text: str) -> dict[str, Any]:
    """Extrae el primer objeto JSON válido de una respuesta de LLM.

    Lanza ``ValueError`` si no hay ningún objeto parseable. El llamador
    (``OllamaProvider.classify_document``) es responsable de aplicar el fallback
    seguro con ``fallback_file_analysis`` ante ese error.
    """
    for parsed in _iter_json_objects(raw_text):
        return parsed

    raise ValueError("No valid JSON object found in Ollama response")


def _unsafe_http_warning(endpoint: str) -> str | None:
    """Devuelve un mensaje si ``endpoint`` usa HTTP en texto plano fuera de local.

    El tráfico HTTP no cifrado expone el prompt (que puede contener previews de
    documentos del usuario) y la API key. Solo se tolera hacia localhost.
    """
    parsed = urlparse(endpoint)
    if parsed.scheme != "http":
        return None
    host = (parsed.hostname or "").lower()
    if host in LOCALHOST_HOSTS or host.startswith("127."):
        return None
    return (
        f"OLLAMA_ENDPOINT usa HTTP sin cifrado hacia '{host}': no es apto para "
        "producción (prompt y API key viajarían en texto plano). Usá HTTPS."
    )


class OllamaProvider:
    def __init__(
        self,
        model: str = MODEL_NAME,
        endpoint: str | None = None,
        request_timeout_s: int = DEFAULT_REQUEST_TIMEOUT_S,
        retries: int = DEFAULT_RETRIES,
        audit_logger: AuditLogger | None = None,
        api_key: str | None = None,
    ):
        # La configuración por entorno permite apuntar a un Ollama remoto sin
        # tocar código; los valores actuales se mantienen como default.
        self.model = model
        self.endpoint = endpoint or os.getenv("OLLAMA_ENDPOINT", OLLAMA_ENDPOINT)
        self.api_key = api_key if api_key is not None else os.getenv("OLLAMA_API_KEY")
        self.request_timeout_s = request_timeout_s
        self.retries = max(1, int(retries))
        self.audit_logger = audit_logger

        warning = _unsafe_http_warning(self.endpoint)
        if warning:
            _LOGGER.warning(warning)
            if self.audit_logger:
                self.audit_logger.log_event("INSECURE_ENDPOINT", {"endpoint": self.endpoint})

    def _request_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate(self, prompt: str) -> str:
        last_error: Exception | None = None

        for retry in range(self.retries):
            started = time.perf_counter()
            response_text: str | None = None
            error_text: str | None = None

            try:
                response = requests.post(
                    self.endpoint,
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                    headers=self._request_headers(),
                    timeout=self.request_timeout_s,
                )
                duration = time.perf_counter() - started

                if not 200 <= getattr(response, "status_code", 0) < 300:
                    raise OllamaProviderError(
                        f"Ollama HTTP {getattr(response, 'status_code', 'unknown')}: "
                        f"{getattr(response, 'text', '')}"
                    )

                response_text = self._extract_response_text(response)
                self._log_llm(
                    prompt=prompt,
                    response=response_text,
                    duration=duration,
                    retry=retry,
                    error=None,
                    fallback=False,
                )
                return response_text

            except Exception as exc:
                duration = time.perf_counter() - started
                last_error = exc
                error_text = str(exc)
                self._log_llm(
                    prompt=prompt,
                    response=response_text,
                    duration=duration,
                    retry=retry,
                    error=error_text,
                    fallback=False,
                )

        raise OllamaProviderError(f"Ollama generation failed after {self.retries} retries: {last_error}")

    def classify_document(
        self,
        original_filename: str,
        preview_text: str | None = None,
        ext: str | None = None,
    ) -> FileAnalysis:
        prompt = self._build_file_analysis_prompt(original_filename, preview_text, ext)
        started = time.perf_counter()

        try:
            raw = self.generate(prompt)
            # fallback_to provee una red de seguridad: si el JSON viene corrupto
            # se devuelve un análisis de fallback en lugar de abortar el scan.
            analysis = parse_file_analysis_json(raw, fallback_to=original_filename)
            if not analysis.suggested_name:
                raise ValueError("suggested_name is empty")
            return analysis
        except (OllamaProviderError, ValidationError, ValueError, TypeError) as exc:
            fallback = fallback_file_analysis(original_filename)
            self._log_llm(
                prompt=prompt,
                response=fallback.model_dump(),
                duration=time.perf_counter() - started,
                retry=self.retries,
                error=str(exc),
                fallback=True,
            )
            return fallback

    def suggest_filename(
        self,
        original_filename: str,
        preview_text: str | None = None,
        ext: str | None = None,
    ) -> str:
        analysis = self.classify_document(original_filename, preview_text, ext)
        filename = safe_filename(analysis.suggested_name)
        original_ext = (ext or os.path.splitext(original_filename)[1] or "").strip()
        if original_ext and not filename.lower().endswith(original_ext.lower()):
            filename = safe_filename(f"{filename}{original_ext}")
        return filename

    def _extract_response_text(self, response: Any) -> str:
        raw_text = getattr(response, "text", "")

        try:
            payload = response.json()
        except Exception:
            payload = None

        if isinstance(payload, dict):
            model_response = payload.get("response")
            if isinstance(model_response, str):
                return model_response.strip()
            if isinstance(model_response, dict):
                return json.dumps(model_response, ensure_ascii=False)
            return json.dumps(payload, ensure_ascii=False)

        try:
            payload = json.loads(raw_text)
            if isinstance(payload, dict):
                model_response = payload.get("response")
                if isinstance(model_response, str):
                    return model_response.strip()
                return json.dumps(payload, ensure_ascii=False)
        except json.JSONDecodeError:
            pass

        chunks: list[str] = []
        for line in str(raw_text).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(chunk, dict) and isinstance(chunk.get("response"), str):
                chunks.append(chunk["response"])
        if chunks:
            return "".join(chunks).strip()

        return str(raw_text).strip()

    def _build_file_analysis_prompt(
        self,
        original_filename: str,
        preview_text: str | None,
        ext: str | None,
    ) -> str:
        preview = (preview_text or "")[:PROMPT_PREVIEW_MAX_CHARS]
        return (
            "Analiza este archivo y responde solamente JSON valido, sin markdown ni texto extra.\n"
            "El JSON debe tener exactamente estas claves: category, suggested_name, reason.\n"
            'Ejemplo: {"category":"invoice","suggested_name":"2026_Invoice_Amazon",'
            '"reason":"Factura emitida por Amazon"}\n'
            f"Nombre original: {original_filename}\n"
            f"Extension: {ext or ''}\n"
            f"Contenido parcial:\n{preview}\n"
        )

    def _log_llm(
        self,
        prompt: str,
        response: Any,
        duration: float,
        retry: int,
        error: str | None,
        fallback: bool,
    ) -> None:
        if not self.audit_logger:
            return
        self.audit_logger.log_llm_interaction(
            prompt=prompt,
            response=response,
            duration=duration,
            retry=retry,
            error=error,
            fallback=fallback,
            model=self.model,
            endpoint=self.endpoint,
        )


class LLMClient(Protocol):
    def propose_plan(self, prompt: str) -> str:  # returns raw text
        ...


class StubLLMClient:
    """LLM stub para MVP sin integración real.

    Regla simple:
    - Para PDFs: renombrar a YYYYMMDD_Original_SinExt.pdf si detecta 8 dígitos en el nombre.
    - Crear carpeta por extensión (ej: /PDFs).

    Importante: reemplaza este stub por Ollama/llama.cpp/OpenAI en fase siguiente.
    """

    def propose_plan(self, prompt: str) -> str:
        return prompt  # unused in this stub


def _extract_date_from_filename(filename: str) -> str | None:
    import re

    m = re.search(r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})", filename)
    if m:
        y, mo, d = m.groups()
        return f"{y}{mo}{d}"
    m2 = re.search(r"\b(\d{8})\b", filename)
    if m2:
        return m2.group(1)
    return None


def build_prompt(planner_input: PlannerInput) -> str:
    # MVP: prompt informativo; el stub no lo usa.
    files = [
        {
            "path": f.path,
            "ext": f.ext,
            "filename": f.filename,
"preview_text": (
                f.preview_text[:PLANNER_PROMPT_PREVIEW_MAX_CHARS] if f.preview_text else None
            ),
        }
        for f in planner_input.files
    ]

    return (
        "Eres un planner para un sistema tipo Claude Cowork. "
        "Genera un plan SEGURO (solo mkdir y rename), y NUNCA borres.\n"
        "Devuelve JSON con: summary, create_dirs[], rename_files[].\n"
        "Reglas: 'src' y 'dst' deben ser rutas absolutas dentro del root.\n"
        f"ROOT: {planner_input.root_dir}\n"
        f"FILES: {files}\n"
    )


def propose_execution_plan(planner_input: PlannerInput, llm: OllamaProvider | None = None) -> PlannerOutput:
    """Genera ExecutionPlan usando el LLM SOLO para sugerencias.

    El LLM nunca decide acciones del sistema: solo clasifica y sugiere nombres.
    La ejecución real depende de Human-in-the-Loop + os_commands.
    """

    provider = llm or OllamaProvider(audit_logger=None)

    pdfs = [f for f in planner_input.files if (f.ext or "").lower() == ".pdf"]
    creates: list[CreateDirAction] = []
    renames: list[RenameAction] = []

    from pathlib import Path

    pdf_dir = str((Path(planner_input.root_dir) / "PDFs").resolve())
    creates.append(CreateDirAction(dir_path=pdf_dir, reason="Separación por tipo"))

    for f in pdfs:
        src = Path(f.path)
        analysis = provider.classify_document(
            original_filename=src.name,
            preview_text=f.preview_text,
            ext=f.ext,
        )

        # FileAnalysis
        suggested_name = analysis.suggested_name
        suggested_name = safe_filename(suggested_name)

        # asegurar extensión
        if (f.ext or "").lower() == ".pdf" and not suggested_name.lower().endswith(".pdf"):
            suggested_name = os.path.splitext(suggested_name)[0] + ".pdf"

        dst = (Path(pdf_dir) / suggested_name).resolve()

        if dst != src.resolve():
            renames.append(
                RenameAction(
                    src=str(src.resolve()),
                    dst=str(dst),
                    reason=f"LLM: {analysis.category} ({analysis.reason})",
                )
            )

    summary = (
        f"He encontrado {len(planner_input.files)} archivos. "
        f"Voy a crear {len(creates)} carpeta(s) y renombrar {len(renames)} archivo(s)."
    )

    plan = ExecutionPlan(summary=summary, create_dirs=creates, rename_files=renames)
    return PlannerOutput(plan=plan)


