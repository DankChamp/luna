"""
Distributed tracing for cross-service requests.
"""
from __future__ import annotations
import uuid
import contextvars
from typing import Any, Optional
from dataclasses import dataclass, field
from contextlib import contextmanager
from typing import Optional
import time


# Context variables for trace propagation
trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
span_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("span_id", default=None)
parent_span_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("parent_span_id", default=None)


@dataclass
class TraceContext:
    """Distributed trace context."""
    trace_id: str
    span_id: str
    parent_span_id: str | None = None
    service_name: str = ""
    operation: str = ""
    start_time: float = field(default_factory=time.time)
    tags: dict[str, str] = field(default_factory=dict)
    logs: list[dict] = field(default_factory=list)
    
    def finish(self) -> dict[str, Any]:
        """Finish the span and return span data."""
        duration = time.time() - self.start_time
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "service_name": self.service_name,
            "operation": self.operation,
            "start_time": self.start_time,
            "duration_ms": round((time.time() - self.start_time) * 1000, 2),
            "tags": self.tags,
            "logs": self.logs,
            "error": any(log.get("level") == "error" for log in self.logs),
        }
    
    def log(self, message: str, level: str = "info", **fields):
        """Add a log entry to this span."""
        self.logs.append({
            "timestamp": time.time(),
            "level": level,
            "message": message,
            **fields,
        })
    
    def set_tag(self, key: str, value: str):
        """Set a tag on this span."""
        self.tags[key] = value
    
    def set_error(self, error: Exception):
        """Mark span as errored."""
        self.log(str(error), level="error", error_type=type(error).__name__)
        self.tags["error"] = "true"


class Tracer:
    """Distributed tracer for creating and managing spans."""
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self._spans: list[TraceContext] = []
        self._lock = __import__("threading").RLock()
    
    def start_span(
        self,
        operation: str,
        parent_context: TraceContext | None = None,
        tags: dict[str, str] | None = None,
    ) -> TraceContext:
        """Start a new span."""
        trace_id = parent_context.trace_id if parent_context else str(__import__("uuid").uuid4())
        parent_span_id = parent_context.span_id if parent_context else None
        
        span = TraceContext(
            trace_id=trace_id,
            span_id=str(__import__("uuid").uuid4())[:16],
            parent_span_id=parent_span_id,
            service_name=self.service_name,
            operation=operation,
            tags=tags or {},
        )
        
        with self._lock:
            self._spans.append(span)
        
        return span
    
    @contextmanager
    def span(self, operation: str, **tags):
        """Context manager for automatic span management."""
        span = self.start_span(operation, tags=tags)
        trace_id_var.set(span.trace_id)
        span_id_var.set(span.span_id)
        parent_span_id_var.set(span.parent_span_id)
        
        try:
            yield span
        except Exception as e:
            span.set_error(e)
            raise
        finally:
            span.finish()
            trace_id_var.set(None)
            span_id_var.set(None)
            parent_span_id_var.set(None)
    
    def get_active_span(self) -> Optional[TraceContext]:
        """Get the currently active span from context."""
        span_id = span_id_var.get()
        if not span_id:
            return None
        
        with self._lock:
            for span in reversed(self._spans):
                if span.span_id == span_id:
                    return span
        return None
    
    def get_finished_spans(self) -> list[dict]:
        """Get all finished spans as dicts."""
        with self._lock:
            return [s.finish() for s in self._spans if time.time() - s.start_time > 0.001]
    
    def clear_finished(self):
        """Remove finished spans from memory."""
        with self._lock:
            now = time.time()
            self._spans = [s for s in self._spans if now - s.start_time < 300]  # Keep last 5 min


# Global tracers per service
_tracers: dict[str, Tracer] = {}


def get_tracer(service_name: str) -> Tracer:
    """Get or create a tracer for the given service."""
    if service_name not in _tracers:
        _tracers[service_name] = Tracer(service_name)
    return _tracers[service_name]


@contextmanager
def trace_span(
    service_name: str,
    operation: str,
    parent_context: TraceContext | None = None,
    **tags,
):
    """Context manager for tracing an operation."""
    tracer = get_tracer(service_name)
    with tracer.span(operation, parent_context=parent_context, tags=tags) as span:
        yield span


def inject_trace_context(headers: dict[str, str]) -> dict[str, str]:
    """Inject trace context into headers for propagation."""
    trace_id = trace_id_var.get()
    span_id = span_id_var.get()
    parent_span_id = parent_span_id_var.get()
    
    if trace_id:
        headers["x-trace-id"] = trace_id
    if span_id:
        headers["x-span-id"] = span_id
    if parent_span_id:
        headers["x-parent-span-id"] = parent_span_id
    
    return headers


def extract_trace_context(headers: dict[str, str]) -> TraceContext | None:
    """Extract trace context from headers."""
    trace_id = headers.get("x-trace-id")
    span_id = headers.get("x-span-id")
    parent_span_id = headers.get("x-parent-span-id")
    
    if not trace_id:
        return None
    
    return TraceContext(
        trace_id=trace_id,
        span_id=span_id or str(__import__("uuid").uuid4())[:16],
        parent_span_id=parent_span_id,
    )