"""Private OpenTelemetry SDK owner, instrumentation, redaction, and GenAI spans.

Tracing is disabled by default (``ENABLE_TRACING=False``). When disabled, no
``TracerProvider`` is constructed or installed and no instrumentation hooks are
attached. When enabled, this module owns a single private ``TracerProvider``
without modifying OpenTelemetry's global provider so Langfuse remains isolated.

All exported spans pass through :class:`SanitizingSpanProcessor`, which drops
sensitive/high-cardinality attributes (prompts, completions, source text, raw
tool arguments, SQL statements, query strings, authorization headers, user IDs)
and redacts credential patterns, configured secrets, and PII before export.
"""

from __future__ import annotations

import functools
import os
import re
import threading
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Final, ParamSpec, TypeVar
from urllib.parse import urlsplit, urlunsplit

import httpx
from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import (
    Event,
    ReadableSpan,
    Span,
    SpanProcessor,
    TracerProvider,
)
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import SpanKind, Status, StatusCode, Tracer
from sqlalchemy import event as sa_event
from structlog import get_logger

from src.core.contracts.redaction import (
    MIN_REDACTABLE_SECRET_LENGTH,
    REDACTION_MARKER,
    is_credential_key,
    redact,
)
from src.core.pii_redactor import redact_pii

logger = get_logger(__name__)

# Pinned OpenTelemetry semantic convention schema URLs.
SEMCONV_SCHEMA_URL: Final[str] = "https://opentelemetry.io/schemas/1.29.0"
GENAI_SEMCONV_SCHEMA_URL: Final[str] = (
    "https://opentelemetry.io/schemas/semconv-genai-dev/0.2.0-unreleased"
)

_MAX_ATTR_CHARS: Final[int] = 512
_TRUTHY_ENV: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_SQL_VERB_RE: Final[re.Pattern[str]] = re.compile(r"^\s*([A-Za-z]+)")

_BLOCKED_ATTRIBUTE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "arguments",
        "body",
        "completion",
        "db.query.text",
        "db.statement",
        "document_text",
        "enduser.id",
        "gen_ai.completion",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
        "gen_ai.prompt",
        "gen_ai.system_instructions",
        "gen_ai.tool.call.arguments",
        "gen_ai.tool.call.result",
        "http.query",
        "http.request.body",
        "http.response.body",
        "input",
        "llm.completions",
        "llm.prompts",
        "output",
        "prompt",
        "raw_arguments",
        "request_body",
        "response_body",
        "source_text",
        "tool_arguments",
        "url.query",
        "user.id",
    }
)

_BLOCKED_ATTRIBUTE_PREFIXES: Final[tuple[str, ...]] = (
    "gen_ai.content.",
    "gen_ai.prompt.",
    "gen_ai.completion.",
    "http.request.header.",
    "http.response.header.",
)

_URL_ATTRIBUTE_KEYS: Final[frozenset[str]] = frozenset(
    {"url.full", "http.url", "http.target"}
)

_SECRET_SETTINGS_FIELDS: Final[tuple[str, ...]] = (
    "SECRET_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "LANGFUSE_SECRET_KEY",
    "DOCUMENT_ENCRYPTION_KEY",
    "OPENALEX_API_KEY",
)

# Private module-level owner state, guarded by _otel_lock.
_otel_lock = threading.Lock()
_tracer_provider: TracerProvider | None = None
_instrumented_apps: list[Any] = []
_instrumented_sa_listeners: list[tuple[Any, str, Callable[..., Any]]] = []
_httpx_instrumented = False
_orig_httpx_sync_send: Callable[..., Any] | None = None
_orig_httpx_async_send: Callable[..., Any] | None = None


def _read_settings() -> Any | None:
    """Read Settings lazily so early module imports never trigger Settings validation."""
    try:
        from src.core.config import get_settings

        return get_settings()
    except (ImportError, ValueError, AttributeError):
        return None


def is_otel_enabled() -> bool:
    """Return True only when ENABLE_TRACING is True and OTEL_SDK_DISABLED is not set."""
    if os.environ.get("OTEL_SDK_DISABLED", "").strip().lower() in _TRUTHY_ENV:
        return False
    settings = _read_settings()
    if settings is None:
        return False
    return bool(getattr(settings, "ENABLE_TRACING", False))


def normalize_otlp_traces_endpoint(endpoint: str) -> str:
    """Normalize an OTLP HTTP base or signal URL to a traces endpoint URL."""
    cleaned = endpoint.strip().rstrip("/")
    if cleaned.endswith("/v1/traces"):
        return cleaned
    return f"{cleaned}/v1/traces"


def _default_secret_values() -> frozenset[str]:
    settings = _read_settings()
    if settings is None:
        return frozenset()
    values: set[str] = set()
    for field_name in _SECRET_SETTINGS_FIELDS:
        raw = getattr(settings, field_name, None)
        if not isinstance(raw, str):
            continue
        # Comma-separated key lists (e.g. rotated DOCUMENT_ENCRYPTION_KEY) are
        # split so each individual key is scrubbed.
        for candidate in raw.split(","):
            stripped = candidate.strip()
            if len(stripped) >= MIN_REDACTABLE_SECRET_LENGTH:
                values.add(stripped)
    return frozenset(values)


def _is_blocked_attribute_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _BLOCKED_ATTRIBUTE_KEYS:
        return True
    if any(lowered.startswith(prefix) for prefix in _BLOCKED_ATTRIBUTE_PREFIXES):
        return True
    if is_credential_key(lowered):
        return True
    return any(is_credential_key(segment) for segment in lowered.split("."))


def _strip_url_sensitive_parts(value: str) -> str:
    """Remove query string, fragment, and embedded credentials from a URL string."""
    if value.startswith("/"):
        return value.split("?", 1)[0].split("#", 1)[0]
    parsed = urlsplit(value)
    if not parsed.scheme and not parsed.netloc:
        return value.split("?", 1)[0].split("#", 1)[0]
    netloc = parsed.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _redact_string(value: str, known_secrets: frozenset[str]) -> str:
    scrubbed = redact(value, known_secret_values=known_secrets)
    text_val = scrubbed if isinstance(scrubbed, str) else str(scrubbed)
    text_val = redact_pii(text_val)
    if len(text_val) > _MAX_ATTR_CHARS:
        return text_val[:_MAX_ATTR_CHARS]
    return text_val


def _sanitize_attributes(
    attributes: Mapping[str, Any] | None,
    known_secrets: frozenset[str],
) -> dict[str, Any]:
    if not attributes:
        return {}
    clean: dict[str, Any] = {}
    for key, value in attributes.items():
        key_str = str(key)
        if _is_blocked_attribute_key(key_str):
            continue
        if isinstance(value, str):
            candidate = (
                _strip_url_sensitive_parts(value)
                if key_str.lower() in _URL_ATTRIBUTE_KEYS
                else value
            )
            clean[key_str] = _redact_string(candidate, known_secrets)
        elif isinstance(value, bytes | bytearray):
            clean[key_str] = REDACTION_MARKER
        elif isinstance(value, Sequence) and not isinstance(value, str):
            sanitized_items = [
                _redact_string(item, known_secrets) if isinstance(item, str) else item
                for item in value
                if isinstance(item, str | bool | int | float)
            ]
            clean[key_str] = tuple(sanitized_items)
        elif isinstance(value, bool | int | float):
            clean[key_str] = value
    return clean


def _sanitize_span(
    span: ReadableSpan,
    known_secrets: frozenset[str],
) -> ReadableSpan:
    """Return a sanitized copy of ``span`` safe for external export."""
    clean_name = _redact_string(span.name, known_secrets)
    clean_attrs = _sanitize_attributes(span.attributes, known_secrets)
    clean_events = tuple(
        Event(
            name=_redact_string(ev.name, known_secrets),
            attributes=_sanitize_attributes(ev.attributes, known_secrets),
            timestamp=ev.timestamp,
        )
        for ev in span.events
    )
    status = span.status
    if status.description:
        status = Status(
            status_code=status.status_code,
            description=_redact_string(status.description, known_secrets),
        )
    return ReadableSpan(
        name=clean_name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=clean_attrs,
        events=clean_events,
        links=span.links,
        kind=span.kind,
        status=status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class _GuardedSpanExporter(SpanExporter):
    """Isolate the application from any exception raised by an underlying exporter."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            return self._inner.export(spans)
        except Exception as exc:
            logger.warning("otel_span_export_failed", error_type=type(exc).__name__)
            return SpanExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return bool(self._inner.force_flush(timeout_millis=timeout_millis))
        except Exception as exc:
            logger.warning("otel_span_flush_failed", error_type=type(exc).__name__)
            return False

    def shutdown(self) -> None:
        try:
            self._inner.shutdown()
        except Exception as exc:
            logger.warning(
                "otel_exporter_shutdown_failed", error_type=type(exc).__name__
            )


class SanitizingSpanProcessor(SpanProcessor):
    """Sanitize every span before delegating to the export processor."""

    def __init__(
        self,
        delegate: SpanProcessor,
        *,
        secret_values_provider: Callable[[], frozenset[str]] | None = None,
    ) -> None:
        self._delegate = delegate
        self._secret_values_provider = secret_values_provider or _default_secret_values

    def on_start(
        self,
        span: Span,
        parent_context: otel_context.Context | None = None,
    ) -> None:
        try:
            self._delegate.on_start(span, parent_context=parent_context)
        except Exception as exc:
            logger.warning(
                "otel_processor_on_start_failed", error_type=type(exc).__name__
            )

    def on_end(self, span: ReadableSpan) -> None:
        try:
            known_secrets = self._secret_values_provider()
            sanitized = _sanitize_span(span, known_secrets)
            self._delegate.on_end(sanitized)
        except Exception as exc:
            logger.warning(
                "otel_processor_on_end_failed", error_type=type(exc).__name__
            )

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        try:
            return bool(self._delegate.force_flush(timeout_millis=timeout_millis))
        except Exception as exc:
            logger.warning("otel_processor_flush_failed", error_type=type(exc).__name__)
            return False

    def shutdown(self) -> None:
        try:
            self._delegate.shutdown()
        except Exception as exc:
            logger.warning(
                "otel_processor_shutdown_failed", error_type=type(exc).__name__
            )


def _sql_operation(statement: str) -> str:
    match = _SQL_VERB_RE.match(statement or "")
    return match.group(1).upper() if match else "QUERY"


def _instrument_fastapi_app(app: Any, provider: TracerProvider) -> None:
    if app in _instrumented_apps:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider,
        exclude_spans=["receive", "send"],
    )
    # Starlette builds middleware_stack before lifespan runs, so rebuild it here
    # so the instrumentation middleware wraps subsequent requests.
    app.middleware_stack = app.build_middleware_stack()
    _instrumented_apps.append(app)


def _instrument_sqlalchemy_engine(engine: Any, provider: TracerProvider) -> None:
    sync_engine = getattr(engine, "sync_engine", engine)
    if any(target is sync_engine for target, _, _ in _instrumented_sa_listeners):
        return

    tracer = provider.get_tracer("cerebro.sqlalchemy", schema_url=SEMCONV_SCHEMA_URL)
    db_namespace = getattr(getattr(sync_engine, "url", None), "database", None)

    def before_cursor_execute(
        conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if otel_context.get_value(_SUPPRESS_INSTRUMENTATION_KEY):
            return
        dialect_name = getattr(getattr(conn, "dialect", None), "name", "sql")
        op = _sql_operation(statement)
        attrs: dict[str, Any] = {
            "db.system": dialect_name,
            "db.operation.name": op,
        }
        if isinstance(db_namespace, str) and db_namespace:
            attrs["db.namespace"] = db_namespace
        span = tracer.start_span(
            f"{op} {dialect_name}",
            kind=SpanKind.CLIENT,
            attributes=attrs,
        )
        token = otel_context.attach(trace.set_span_in_context(span))
        conn.info.setdefault("_cerebro_otel_spans", []).append((span, token))

    def after_cursor_execute(
        conn: Any,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        stack = conn.info.get("_cerebro_otel_spans")
        if not stack:
            return
        span, token = stack.pop()
        try:
            span.end()
        finally:
            otel_context.detach(token)

    def handle_error(exception_context: Any) -> None:
        conn = getattr(exception_context, "connection", None)
        if conn is None:
            return
        stack = conn.info.get("_cerebro_otel_spans")
        if not stack:
            return
        span, token = stack.pop()
        try:
            orig = getattr(exception_context, "original_exception", None)
            err_type = type(orig).__name__ if orig is not None else "DatabaseError"
            span.set_attribute("error.type", err_type)
            span.set_status(Status(StatusCode.ERROR, err_type))
            span.end()
        finally:
            otel_context.detach(token)

    for event_name, listener in (
        ("before_cursor_execute", before_cursor_execute),
        ("after_cursor_execute", after_cursor_execute),
        ("handle_error", handle_error),
    ):
        sa_event.listen(sync_engine, event_name, listener)
        _instrumented_sa_listeners.append((sync_engine, event_name, listener))


def _httpx_span_attributes(request: httpx.Request) -> dict[str, Any]:
    url = request.url
    attrs: dict[str, Any] = {
        "http.request.method": request.method,
        "url.scheme": url.scheme,
        "url.path": url.path,
    }
    if url.host:
        attrs["server.address"] = url.host
    if url.port is not None:
        attrs["server.port"] = url.port
    return attrs


def _instrument_httpx() -> None:
    global _httpx_instrumented, _orig_httpx_sync_send, _orig_httpx_async_send
    if _httpx_instrumented:
        return

    orig_sync = httpx.Client.send
    orig_async = httpx.AsyncClient.send
    _orig_httpx_sync_send = orig_sync
    _orig_httpx_async_send = orig_async

    def traced_sync_send(
        self: httpx.Client, request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        provider = _tracer_provider
        if provider is None or otel_context.get_value(_SUPPRESS_INSTRUMENTATION_KEY):
            return orig_sync(self, request, *args, **kwargs)
        tracer = provider.get_tracer("cerebro.httpx", schema_url=SEMCONV_SCHEMA_URL)
        span_name = f"{request.method} {request.url.host or ''}".strip()
        with tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=_httpx_span_attributes(request),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            propagate.inject(request.headers)
            try:
                response = orig_sync(self, request, *args, **kwargs)
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            return response

    async def traced_async_send(
        self: httpx.AsyncClient, request: httpx.Request, *args: Any, **kwargs: Any
    ) -> httpx.Response:
        provider = _tracer_provider
        if provider is None or otel_context.get_value(_SUPPRESS_INSTRUMENTATION_KEY):
            return await orig_async(self, request, *args, **kwargs)
        tracer = provider.get_tracer("cerebro.httpx", schema_url=SEMCONV_SCHEMA_URL)
        span_name = f"{request.method} {request.url.host or ''}".strip()
        with tracer.start_as_current_span(
            span_name,
            kind=SpanKind.CLIENT,
            attributes=_httpx_span_attributes(request),
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            propagate.inject(request.headers)
            try:
                response = await orig_async(self, request, *args, **kwargs)
            except Exception as exc:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            span.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            return response

    httpx.Client.send = traced_sync_send  # type: ignore[method-assign]
    httpx.AsyncClient.send = traced_async_send  # type: ignore[method-assign]
    _httpx_instrumented = True


def _uninstrument_all(
    apps: Sequence[Any],
    sa_listeners: Sequence[tuple[Any, str, Callable[..., Any]]],
    restore_httpx: tuple[Callable[..., Any] | None, Callable[..., Any] | None],
) -> None:
    if apps:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        for app in apps:
            try:
                FastAPIInstrumentor.uninstrument_app(app)
            except Exception as exc:
                logger.warning(
                    "otel_fastapi_uninstrument_failed", error_type=type(exc).__name__
                )

    for sync_engine, event_name, listener in sa_listeners:
        try:
            if sa_event.contains(sync_engine, event_name, listener):
                sa_event.remove(sync_engine, event_name, listener)
        except Exception as exc:
            logger.warning(
                "otel_sqlalchemy_uninstrument_failed", error_type=type(exc).__name__
            )

    orig_sync, orig_async = restore_httpx
    if orig_sync is not None:
        httpx.Client.send = orig_sync  # type: ignore[method-assign]
    if orig_async is not None:
        httpx.AsyncClient.send = orig_async  # type: ignore[method-assign]


def init_otel(
    *,
    app: Any | None = None,
    engines: Sequence[Any] = (),
    span_exporter: SpanExporter | None = None,
    secret_values_provider: Callable[[], frozenset[str]] | None = None,
    force_enable: bool = False,
) -> bool:
    """Initialize the private OpenTelemetry TracerProvider and instrumentors.

    Returns False without allocating a provider or touching any instrumentor
    when tracing is disabled.
    """
    global _tracer_provider

    if not force_enable and not is_otel_enabled():
        return False

    with _otel_lock:
        if _tracer_provider is None:
            settings = _read_settings()
            service_name = (
                getattr(settings, "OTEL_SERVICE_NAME", "cerebro-api")
                if settings is not None
                else "cerebro-api"
            )
            endpoint = (
                getattr(settings, "OTEL_EXPORTER_OTLP_ENDPOINT", None)
                if settings is not None
                else None
            )

            # shutdown_on_exit=False prevents an unreachable collector from
            # hanging process exit; lifespan drives bounded shutdown instead.
            provider = TracerProvider(
                resource=Resource.create({"service.name": service_name}),
                shutdown_on_exit=False,
            )

            processor: SpanProcessor | None = None
            if span_exporter is not None:
                processor = SanitizingSpanProcessor(
                    SimpleSpanProcessor(_GuardedSpanExporter(span_exporter)),
                    secret_values_provider=secret_values_provider,
                )
            elif isinstance(endpoint, str) and endpoint.strip():
                try:
                    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                        OTLPSpanExporter,
                    )

                    otlp_exporter = OTLPSpanExporter(
                        endpoint=normalize_otlp_traces_endpoint(endpoint),
                        timeout=5.0,
                    )
                    processor = SanitizingSpanProcessor(
                        BatchSpanProcessor(
                            _GuardedSpanExporter(otlp_exporter),
                            export_timeout_millis=5000,
                            schedule_delay_millis=2000,
                        ),
                        secret_values_provider=secret_values_provider,
                    )
                except ImportError:
                    logger.warning("otel_otlp_exporter_not_installed")
                except Exception as exc:
                    logger.warning(
                        "otel_otlp_exporter_init_failed",
                        error_type=type(exc).__name__,
                    )
            else:
                logger.warning("otel_endpoint_not_configured")

            if processor is not None:
                provider.add_span_processor(processor)

            _tracer_provider = provider

        active_provider = _tracer_provider
        if app is not None:
            _instrument_fastapi_app(app, active_provider)
        for engine in engines:
            if engine is not None:
                _instrument_sqlalchemy_engine(engine, active_provider)
        _instrument_httpx()
        return True


def shutdown_otel(*, timeout_seconds: float = 5.0) -> None:
    """Uninstrument targets and shut down the private TracerProvider with a bound."""
    global _tracer_provider, _httpx_instrumented
    global _orig_httpx_sync_send, _orig_httpx_async_send

    with _otel_lock:
        provider = _tracer_provider
        apps = list(_instrumented_apps)
        sa_listeners = list(_instrumented_sa_listeners)
        restore_httpx = (
            (_orig_httpx_sync_send, _orig_httpx_async_send)
            if _httpx_instrumented
            else (None, None)
        )
        _tracer_provider = None
        _instrumented_apps.clear()
        _instrumented_sa_listeners.clear()
        _httpx_instrumented = False
        _orig_httpx_sync_send = None
        _orig_httpx_async_send = None

    _uninstrument_all(apps, sa_listeners, restore_httpx)

    if provider is None:
        return

    timeout_millis = max(int(timeout_seconds * 1000), 1)

    def _finish() -> None:
        try:
            provider.force_flush(timeout_millis=timeout_millis)
        except Exception as exc:
            logger.warning("otel_force_flush_failed", error_type=type(exc).__name__)
        try:
            provider.shutdown()
        except Exception as exc:
            logger.warning(
                "otel_provider_shutdown_failed", error_type=type(exc).__name__
            )

    worker = threading.Thread(target=_finish, name="otel-shutdown", daemon=True)
    worker.start()
    worker.join(timeout=timeout_seconds)
    if worker.is_alive():
        logger.warning("otel_shutdown_timed_out", timeout_seconds=timeout_seconds)


def reset_otel_state() -> None:
    """Reset module state cleanly. Intended for tests."""
    shutdown_otel(timeout_seconds=1.0)


def get_otel_tracer_provider() -> TracerProvider | None:
    """Return the active private TracerProvider, or None when tracing is off."""
    return _tracer_provider


def get_otel_tracer(
    name: str = "cerebro",
    *,
    schema_url: str | None = None,
) -> Tracer | None:
    """Return a Tracer from the private provider, or None when tracing is off."""
    provider = _tracer_provider
    if provider is None:
        return None
    return provider.get_tracer(name, schema_url=schema_url)


@contextmanager
def _managed_span(
    span_name: str,
    *,
    kind: SpanKind,
    attributes: Mapping[str, Any],
    tracer_name: str = "cerebro.genai",
    schema_url: str = GENAI_SEMCONV_SCHEMA_URL,
) -> Iterator[trace.Span | None]:
    tracer = get_otel_tracer(tracer_name, schema_url=schema_url)
    if tracer is None:
        yield None
        return

    span: trace.Span | None = None
    token: Any = None
    try:
        clean_attrs = {k: v for k, v in attributes.items() if v is not None}
        span = tracer.start_span(span_name, kind=kind, attributes=clean_attrs)
        token = otel_context.attach(trace.set_span_in_context(span))
    except Exception as exc:
        logger.warning("otel_span_start_failed", error_type=type(exc).__name__)
        span = None
        token = None

    try:
        yield span
    except BaseException as exc:
        if span is not None:
            try:
                span.set_attribute("error.type", type(exc).__name__)
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            except Exception as span_exc:
                logger.warning(
                    "otel_span_error_record_failed",
                    error_type=type(span_exc).__name__,
                )
        raise
    finally:
        if span is not None:
            try:
                span.end()
            except Exception as exc:
                logger.warning("otel_span_end_failed", error_type=type(exc).__name__)
        if token is not None:
            try:
                otel_context.detach(token)
            except Exception as exc:
                logger.warning(
                    "otel_context_detach_failed", error_type=type(exc).__name__
                )


@contextmanager
def trace_run_span(
    *,
    run_id: str,
    workflow_name: str = "research_workflow",
    workflow_attempt: int = 1,
) -> Iterator[trace.Span | None]:
    """Trace one workflow run attempt under the private OTel provider."""
    with _managed_span(
        f"invoke_workflow {workflow_name}",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_workflow",
            "gen_ai.conversation.id": run_id,
            "cerebro.run_id": run_id,
            "cerebro.workflow_attempt": workflow_attempt,
        },
    ) as span:
        yield span


@contextmanager
def trace_worker_span(
    *,
    agent_name: str,
    run_id: str | None = None,
    task_id: str | None = None,
    worker_id: str | None = None,
) -> Iterator[trace.Span | None]:
    """Trace one worker execution under the private OTel provider."""
    with _managed_span(
        f"invoke_agent {agent_name}",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": agent_name,
            "gen_ai.conversation.id": run_id,
            "cerebro.run_id": run_id,
            "cerebro.task_id": task_id,
            "cerebro.worker_id": worker_id,
        },
    ) as span:
        yield span


@contextmanager
def trace_provider_span(
    *,
    provider: str | None = None,
    model: str | None = None,
    run_id: str | None = None,
    call_id: str | None = None,
    attempt: int | None = None,
) -> Iterator[trace.Span | None]:
    """Trace one governed LLM provider attempt (metadata only)."""
    span_name = f"chat {model}" if model else "chat"
    with _managed_span(
        span_name,
        kind=SpanKind.CLIENT,
        attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": provider,
            "gen_ai.request.model": model,
            "gen_ai.conversation.id": run_id,
            "cerebro.run_id": run_id,
            "cerebro.call_id": call_id,
            "cerebro.attempt": attempt,
        },
    ) as span:
        yield span


def record_provider_span_outcome(
    span: trace.Span | None,
    *,
    provider: str | None = None,
    model: str | None = None,
    response_id: str | None = None,
    finish_reason: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    outcome: str | None = None,
    usage_certainty: str | None = None,
) -> None:
    """Record bounded provider metadata onto an active OTel span."""
    if span is None:
        return
    try:
        if provider:
            span.set_attribute("gen_ai.provider.name", provider)
        if model:
            span.set_attribute("gen_ai.response.model", model)
            span.update_name(f"chat {model}")
        if response_id:
            span.set_attribute("gen_ai.response.id", response_id)
        if finish_reason:
            span.set_attribute("gen_ai.response.finish_reasons", (finish_reason,))
        if input_tokens is not None:
            span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        if output_tokens is not None:
            span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        if outcome:
            span.set_attribute("cerebro.outcome", outcome)
        if usage_certainty:
            span.set_attribute("cerebro.usage_certainty", usage_certainty)
    except Exception as exc:
        logger.warning("otel_provider_outcome_failed", error_type=type(exc).__name__)


@contextmanager
def trace_tool_span(
    *,
    tool_name: str,
    run_id: str,
    task_id: str,
    attempt_id: str,
    tool_invocation_id: str | None = None,
    worker_scope: str | None = None,
) -> Iterator[trace.Span | None]:
    """Trace one mediated tool invocation (metadata only; never arguments)."""
    with _managed_span(
        f"execute_tool {tool_name}",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool_name,
            "gen_ai.tool.type": "function",
            "gen_ai.conversation.id": run_id,
            "cerebro.run_id": run_id,
            "cerebro.task_id": task_id,
            "cerebro.attempt_id": attempt_id,
            "cerebro.tool.invocation_id": tool_invocation_id,
            "cerebro.worker_scope": worker_scope,
        },
    ) as span:
        yield span


def record_tool_span_outcome(
    span: trace.Span | None,
    *,
    outcome_status: str,
    error_code: str | None = None,
) -> None:
    """Record tool outcome metadata onto an active OTel span."""
    if span is None:
        return
    try:
        span.set_attribute("cerebro.tool.outcome_status", outcome_status)
        if error_code:
            span.set_attribute("error.type", error_code)
            span.set_status(Status(StatusCode.ERROR, error_code))
    except Exception as exc:
        logger.warning("otel_tool_outcome_failed", error_type=type(exc).__name__)


_P = ParamSpec("_P")
_R = TypeVar("_R")


def traced_tool_invocation(
    func: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Wrap ``ToolBoundary.invoke`` with an OTel ``execute_tool`` span (metadata only)."""

    @functools.wraps(func)
    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if _tracer_provider is None:
            return await func(*args, **kwargs)
        tool_name = str(kwargs.get("tool_name", "unknown"))
        run_id = str(kwargs.get("run_id", ""))
        task_id = str(kwargs.get("task_id", ""))
        attempt_id = str(kwargs.get("attempt_id", ""))
        worker_scope = kwargs.get("worker_scope")
        with trace_tool_span(
            tool_name=tool_name,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            worker_scope=str(worker_scope) if worker_scope is not None else None,
        ) as span:
            outcome = await func(*args, **kwargs)
            status = getattr(outcome, "status", None)
            status_val = getattr(status, "value", None) or (
                str(status) if status is not None else None
            )
            if status_val is not None:
                record_tool_span_outcome(
                    span,
                    outcome_status=status_val,
                    error_code=getattr(outcome, "error_code", None),
                )
            return outcome

    return wrapper


__all__ = [
    "GENAI_SEMCONV_SCHEMA_URL",
    "SEMCONV_SCHEMA_URL",
    "SanitizingSpanProcessor",
    "get_otel_tracer",
    "get_otel_tracer_provider",
    "init_otel",
    "is_otel_enabled",
    "normalize_otlp_traces_endpoint",
    "record_provider_span_outcome",
    "record_tool_span_outcome",
    "reset_otel_state",
    "shutdown_otel",
    "trace_provider_span",
    "trace_run_span",
    "trace_tool_span",
    "trace_worker_span",
    "traced_tool_invocation",
]
