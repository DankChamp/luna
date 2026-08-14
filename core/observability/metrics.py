"""
Metrics collection for Prometheus and internal monitoring.
"""
from __future__ import annotations
import time
import threading
from typing import Any
from dataclasses import dataclass, field
from collections import defaultdict
from contextlib import contextmanager
from typing import Optional


@dataclass
class MetricPoint:
    """Single metric data point."""
    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """
    In-memory metrics collector with Prometheus-compatible output.
    
    Thread-safe, supports counters, gauges, histograms, and summaries.
    """
    
    def __init__(self):
        self._lock = threading.RLock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._summaries: dict[str, list[float]] = defaultdict(list)
        self._last_flush = time.time()
    
    def increment(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None):
        """Increment a counter."""
        key = self._make_key(name, labels or {})
        with self._lock:
            self._counters[key] += value
    
    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None):
        """Set a gauge value."""
        key = self._make_key(name, labels or {})
        with self._lock:
            self._gauges[key] = value
    
    def observe_histogram(self, name: str, value: float, labels: dict[str, str] | None = None):
        """Record a histogram observation."""
        key = self._make_key(name, labels or {})
        with self._lock:
            self._histograms[key].append(value)
            # Keep only last 1000 observations per key
            if len(self._histograms[key]) > 1000:
                self._histograms[key] = self._histograms[key][-1000:]
    
    def observe_summary(self, name: str, value: float, labels: dict[str, str] | None = None):
        """Record a summary observation (for quantiles)."""
        key = self._make_key(name, labels or {})
        with self._lock:
            self._summaries[key].append(value)
            if len(self._summaries[key]) > 1000:
                self._summaries[key] = self._summaries[key][-1000:]
    
    @contextmanager
    def timer(self, name: str, labels: dict[str, str] | None = None):
        """Context manager to time an operation."""
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            self.observe_histogram(f"{name}_duration_seconds", duration, labels)
    
    def _make_key(self, name: str, labels: dict[str, str]) -> str:
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"
    
    def get_all(self) -> dict[str, Any]:
        """Get all metrics as a dict."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: self._percentiles(v) for k, v in self._histograms.items()},
                "summaries": {k: self._percentiles(v) for k, v in self._summaries.items()},
            }
    
    def _percentiles(self, values: list[float]) -> dict[str, float]:
        if not values:
            return {}
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        return {
            "count": n,
            "sum": sum(sorted_vals),
            "min": sorted_vals[0],
            "max": sorted_vals[-1],
            "p50": sorted_vals[int(n * 0.5)],
            "p90": sorted_vals[int(n * 0.9)],
            "p95": sorted_vals[int(n * 0.95)],
            "p99": sorted_vals[int(n * 0.99)],
        }
    
    def to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        with self._lock:
            # Counters
            for key, value in self._counters.items():
                name, labels = self._parse_key(key)
                lines.append(f"# TYPE {name} counter")
                lines.append(f'{name}{{{labels}}} {value}')
            
            # Gauges
            for key, value in self._gauges.items():
                name, labels = self._parse_key(key)
                lines.append(f"# TYPE {name} gauge")
                lines.append(f'{name}{{{labels}}} {value}')
            
            # Histograms
            for key, values in self._histograms.items():
                if not values:
                    continue
                name, labels = self._parse_key(key)
                lines.append(f"# TYPE {name} histogram")
                percentiles = self._percentiles(values)
                for p_name, p_value in percentiles.items():
                    if p_name in ("count", "sum"):
                        continue
                    lines.append(f'{name}_bucket{{{labels},le="{p_name}"}} {p_value}')
                lines.append(f'{name}_count{{{labels}}} {percentiles.get("count", 0)}')
                lines.append(f'{name}_sum{{{labels}}} {percentiles.get("sum", 0)}')
        
        return "\n".join(lines)
    
    def _parse_key(self, key: str) -> tuple[str, str]:
        if "{" in key:
            name, labels = key.split("{", 1)
            labels = labels.rstrip("}")
            return name, labels
        return key, ""
    
    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._summaries.clear()


# Global metrics instance
_global_metrics: Optional[MetricsCollector] = None


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector."""
    global _global_metrics
    if _global_metrics is None:
        _global_metrics = MetricsCollector()
    return _global_metrics


# Common metric names
class MetricNames:
    # Delegation metrics
    DELEGATION_STARTED = "delegation_started_total"
    DELEGATION_COMPLETED = "delegation_completed_total"
    DELEGATION_FAILED = "delegation_failed_total"
    DELEGATION_DURATION = "delegation_duration_seconds"
    
    # LLM metrics
    LLM_REQUESTS = "llm_requests_total"
    LLM_TOKENS = "llm_tokens_total"
    LLM_LATENCY = "llm_latency_seconds"
    
    # Tool metrics
    TOOL_EXECUTIONS = "tool_executions_total"
    TOOL_DURATION = "tool_duration_seconds"
    TOOL_ERRORS = "tool_errors_total"
    
    # Session metrics
    SESSION_SAVES = "session_saves_total"
    SESSION_COMPACTIONS = "session_compactions_total"
    
    # System metrics
    ACTIVE_CONNECTIONS = "active_connections"
    MEMORY_USAGE = "memory_usage_bytes"