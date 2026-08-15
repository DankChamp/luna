"""
Luna-specific observability setup.
"""
from .logging import setup_logging, get_logger, LogContext
from .metrics import get_metrics, MetricNames
from .tracing import get_tracer, trace_span
from .resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitBreakerOpenError,
    RetryConfig,
    with_retry,
    ResilientClient,
    get_luna_resilient_client,
    get_aqua_resilient_client,
    delegate_to_luna,
)
from .multi_turn import (
    DelegationSession,
    DelegationManager,
    get_delegation_manager,
    delegate_turn,
)
from .telemetry import TelemetryManager, TelemetryConfig, LunaMetrics, get_telemetry_manager, init_telemetry

# Initialize Luna observability
def init_observability(level: str = "INFO", json_format: bool = True):
    """Initialize Luna's observability stack."""
    setup_logging(level=level, json_format=json_format)
    return get_tracer("luna")


__all__ = [
    "setup_logging",
    "get_logger",
    "LogContext",
    "get_metrics",
    "MetricNames",
    "get_tracer",
    "trace_span",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitState",
    "CircuitBreakerOpenError",
    "RetryConfig",
    "with_retry",
    "ResilientClient",
    "get_luna_resilient_client",
    "get_aqua_resilient_client",
    "delegate_to_luna",
    "DelegationSession",
    "DelegationManager",
    "get_delegation_manager",
    "delegate_turn",
    "TelemetryManager",
    "TelemetryConfig",
    "LunaMetrics",
    "get_telemetry_manager",
    "init_telemetry",
    "init_observability",
]