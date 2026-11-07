"""
Observability Integration Module (O-005)

Provides helpers to instrument R4 applications with metrics,
traces, and structured logging for the RiVault observability stack.

Usage:
    from r4.observability import metrics, tracer, setup_logging

    # Metrics
    metrics.counter("api_requests_total", labels={"endpoint": "/chat"})
    metrics.histogram("request_duration_seconds", value=0.345)

    # Tracing
    with tracer.span("my_operation") as span:
        span.set_attribute("user_id", user_id)
        # ... operation code ...

    # Logging
    setup_logging(service_name="my-service")
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# Type for decorated functions
F = TypeVar("F", bound=Callable[..., Any])


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class ObservabilityConfig:
    """Configuration for observability integration."""

    # Service identification
    service_name: str = "r4"
    service_version: str = "0.1.0"
    environment: str = "development"

    # Endpoints
    prometheus_pushgateway: Optional[str] = None
    jaeger_endpoint: Optional[str] = None
    loki_endpoint: Optional[str] = None

    # Sampling
    trace_sample_rate: float = 1.0  # 1.0 = 100% sampling
    metrics_interval: float = 15.0  # Push interval in seconds

    # Features
    auto_instrument_http: bool = True
    auto_instrument_db: bool = True

    # Labels
    default_labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "ObservabilityConfig":
        """Create configuration from environment variables."""
        return cls(
            service_name=os.environ.get("OTEL_SERVICE_NAME", "r4"),
            service_version=os.environ.get("R4_VERSION") or os.environ.get("R4_VERSION", "0.1.0"),
            environment=os.environ.get("ENVIRONMENT", "development"),
            prometheus_pushgateway=os.environ.get("PROMETHEUS_PUSHGATEWAY"),
            jaeger_endpoint=os.environ.get("JAEGER_ENDPOINT"),
            loki_endpoint=os.environ.get("LOKI_ENDPOINT"),
            trace_sample_rate=float(os.environ.get("OTEL_TRACE_SAMPLE_RATE", "1.0")),
        )


# Global config instance
_config: Optional[ObservabilityConfig] = None


def get_config() -> ObservabilityConfig:
    """Get the current observability configuration."""
    global _config
    if _config is None:
        _config = ObservabilityConfig.from_env()
    return _config


def configure(config: ObservabilityConfig) -> None:
    """Set the observability configuration."""
    global _config
    _config = config


# =============================================================================
# Metrics
# =============================================================================


@dataclass
class MetricValue:
    """A single metric observation."""

    name: str
    value: float
    labels: dict[str, str]
    timestamp: datetime
    metric_type: str  # counter, gauge, histogram


class MetricsRegistry:
    """
    Registry for application metrics.

    Collects metrics in-memory for push to Prometheus Pushgateway
    or exposure via /metrics endpoint.
    """

    def __init__(self) -> None:
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}
        self._labels: dict[str, dict[str, str]] = {}

    def _key(self, name: str, labels: Optional[dict[str, str]] = None) -> str:
        """Generate unique key for metric+labels."""
        if not labels:
            return name
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Increment a counter metric."""
        key = self._key(name, labels)
        self._counters[key] = self._counters.get(key, 0) + value
        self._labels[key] = labels or {}

    def gauge(
        self,
        name: str,
        value: float,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Set a gauge metric."""
        key = self._key(name, labels)
        self._gauges[key] = value
        self._labels[key] = labels or {}

    def histogram(
        self,
        name: str,
        value: float,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Observe a histogram value."""
        key = self._key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = []
        self._histograms[key].append(value)
        self._labels[key] = labels or {}

    def get_metrics(self) -> list[MetricValue]:
        """Get all current metric values."""
        metrics = []
        now = datetime.now()

        for key, value in self._counters.items():
            metrics.append(
                MetricValue(
                    name=key.split("{")[0],
                    value=value,
                    labels=self._labels.get(key, {}),
                    timestamp=now,
                    metric_type="counter",
                )
            )

        for key, value in self._gauges.items():
            metrics.append(
                MetricValue(
                    name=key.split("{")[0],
                    value=value,
                    labels=self._labels.get(key, {}),
                    timestamp=now,
                    metric_type="gauge",
                )
            )

        for key, values in self._histograms.items():
            if values:
                metrics.append(
                    MetricValue(
                        name=key.split("{")[0],
                        value=sum(values) / len(values),  # Average
                        labels=self._labels.get(key, {}),
                        timestamp=now,
                        metric_type="histogram",
                    )
                )

        return metrics

    def to_prometheus_format(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        metrics = self.get_metrics()

        for metric in metrics:
            labels_str = ""
            if metric.labels:
                labels_str = "{" + ",".join(f'{k}="{v}"' for k, v in metric.labels.items()) + "}"
            lines.append(f"{metric.name}{labels_str} {metric.value}")

        return "\n".join(lines)


# Global metrics registry
metrics = MetricsRegistry()


def timed(metric_name: str, labels: Optional[dict[str, str]] = None) -> Callable[[F], F]:
    """
    Decorator to time function execution.

    Example:
        @timed("function_duration_seconds", {"function": "process"})
        def process_data():
            ...
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                duration = time.perf_counter() - start
                metrics.histogram(metric_name, duration, labels)

        return wrapper  # type: ignore

    return decorator


def counted(metric_name: str, labels: Optional[dict[str, str]] = None) -> Callable[[F], F]:
    """
    Decorator to count function calls.

    Example:
        @counted("api_calls_total", {"endpoint": "/chat"})
        def handle_chat():
            ...
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            metrics.counter(metric_name, 1.0, labels)
            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


# =============================================================================
# Tracing
# =============================================================================


@dataclass
class Span:
    """A single trace span."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    start_time: float
    end_time: Optional[float] = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "UNSET"

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a span attribute."""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[dict[str, Any]] = None) -> None:
        """Add an event to the span."""
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            }
        )

    def set_status(self, status: str, description: Optional[str] = None) -> None:
        """Set span status."""
        self.status = status
        if description:
            self.attributes["status.description"] = description

    def end(self) -> None:
        """End the span."""
        self.end_time = time.time()

    @property
    def duration_ms(self) -> Optional[float]:
        """Get span duration in milliseconds."""
        if self.end_time:
            return (self.end_time - self.start_time) * 1000
        return None


class Tracer:
    """
    Simple tracer for distributed tracing.

    Compatible with OpenTelemetry export format.
    """

    def __init__(self) -> None:
        self._spans: list[Span] = []
        self._active_span: Optional[Span] = None
        self._trace_id_counter = 0
        self._span_id_counter = 0

    def _generate_trace_id(self) -> str:
        """Generate a trace ID."""
        self._trace_id_counter += 1
        return f"trace-{self._trace_id_counter:016x}"

    def _generate_span_id(self) -> str:
        """Generate a span ID."""
        self._span_id_counter += 1
        return f"span-{self._span_id_counter:016x}"

    @contextmanager
    def span(self, name: str, attributes: Optional[dict[str, Any]] = None):
        """
        Create a span context.

        Example:
            with tracer.span("my_operation") as span:
                span.set_attribute("key", "value")
                # ... operation ...
        """
        parent = self._active_span
        trace_id = parent.trace_id if parent else self._generate_trace_id()

        span = Span(
            name=name,
            trace_id=trace_id,
            span_id=self._generate_span_id(),
            parent_span_id=parent.span_id if parent else None,
            start_time=time.time(),
            attributes=attributes or {},
        )

        self._active_span = span

        try:
            yield span
            span.set_status("OK")
        except Exception as e:
            span.set_status("ERROR", str(e))
            raise
        finally:
            span.end()
            self._spans.append(span)
            self._active_span = parent

    @property
    def active_span(self) -> Optional[Span]:
        """Get the currently active span."""
        return self._active_span

    def get_spans(self) -> list[Span]:
        """Get all recorded spans."""
        return self._spans.copy()

    def clear(self) -> None:
        """Clear recorded spans."""
        self._spans.clear()


# Global tracer instance
tracer = Tracer()


def traced(name: Optional[str] = None) -> Callable[[F], F]:
    """
    Decorator to trace function execution.

    Example:
        @traced("process_data")
        def process_data():
            ...
    """

    def decorator(func: F) -> F:
        span_name = name or func.__name__

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with tracer.span(span_name) as span:
                span.set_attribute("function", func.__name__)
                span.set_attribute("module", func.__module__)
                return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


# =============================================================================
# Structured Logging
# =============================================================================


class StructuredFormatter(logging.Formatter):
    """JSON structured log formatter for Loki compatibility."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        import json

        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add extra fields
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add trace context if available
        if tracer.active_span:
            log_data["trace_id"] = tracer.active_span.trace_id
            log_data["span_id"] = tracer.active_span.span_id

        return json.dumps(log_data)


def setup_logging(
    service_name: str = "r4",
    level: int = logging.INFO,
    structured: bool = True,
) -> None:
    """
    Set up logging for the application.

    Args:
        service_name: Name for log labels
        level: Logging level
        structured: Use JSON format for Loki
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add new handler
    handler = logging.StreamHandler()
    handler.setLevel(level)

    if structured:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(f"[%(asctime)s] [{service_name}] %(levelname)s - %(message)s")
        )

    root_logger.addHandler(handler)


class LogContext:
    """
    Context manager for adding fields to log records.

    Example:
        with LogContext(user_id="123", request_id="abc"):
            logger.info("Processing request")
    """

    _context: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        self._fields = kwargs

    def __enter__(self) -> "LogContext":
        LogContext._context.update(self._fields)
        return self

    def __exit__(self, *args: Any) -> None:
        for key in self._fields:
            LogContext._context.pop(key, None)

    @classmethod
    def get_context(cls) -> dict[str, Any]:
        """Get current log context."""
        return cls._context.copy()


# =============================================================================
# Integration Helpers
# =============================================================================


def instrument_fastapi(app: Any) -> None:
    """
    Add observability middleware to FastAPI application.

    Args:
        app: FastAPI application instance
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response

    class ObservabilityMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: Callable) -> Response:
            with tracer.span(f"HTTP {request.method} {request.url.path}") as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.url", str(request.url))
                span.set_attribute(
                    "http.client_ip", request.client.host if request.client else "unknown"
                )

                start = time.perf_counter()
                try:
                    response = await call_next(request)
                    span.set_attribute("http.status_code", response.status_code)
                    span.set_status("OK")
                except Exception as e:
                    span.set_status("ERROR", str(e))
                    raise
                finally:
                    duration = time.perf_counter() - start
                    metrics.histogram(
                        "http_request_duration_seconds",
                        duration,
                        {"method": request.method, "path": request.url.path},
                    )
                    metrics.counter(
                        "http_requests_total",
                        1.0,
                        {
                            "method": request.method,
                            "path": request.url.path,
                            "status": str(response.status_code) if "response" in dir() else "error",
                        },
                    )

                return response

    app.add_middleware(ObservabilityMiddleware)


def get_health_metrics() -> dict[str, Any]:
    """
    Get health metrics for the application.

    Returns dict suitable for /health endpoint.
    """
    import platform

    return {
        "status": "healthy",
        "service": get_config().service_name,
        "version": get_config().service_version,
        "environment": get_config().environment,
        "timestamp": datetime.now().isoformat(),
        "python_version": platform.python_version(),
        "metrics": {
            "counters": len(metrics._counters),
            "gauges": len(metrics._gauges),
            "histograms": len(metrics._histograms),
        },
        "tracing": {
            "spans_recorded": len(tracer._spans),
            "sample_rate": get_config().trace_sample_rate,
        },
    }


__all__ = [
    # Configuration
    "ObservabilityConfig",
    "get_config",
    "configure",
    # Metrics
    "MetricsRegistry",
    "MetricValue",
    "metrics",
    "timed",
    "counted",
    # Tracing
    "Tracer",
    "Span",
    "tracer",
    "traced",
    # Logging
    "StructuredFormatter",
    "setup_logging",
    "LogContext",
    # Integration
    "instrument_fastapi",
    "get_health_metrics",
]
