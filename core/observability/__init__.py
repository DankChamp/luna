"""
Luna-specific observability setup.
"""
from .logging import LogContext, get_logger, setup_logging
from .metrics import MetricNames, get_metrics
from .multi_turn import (
    DelegationManager,
    DelegationSession,
    delegate_turn,
    get_delegation_manager,
)
from .resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
    ResilientClient,
    RetryConfig,
    delegate_to_luna,
    get_aqua_resilient_client,
    get_luna_resilient_client,
    with_retry,
)
from .telemetry import (
    LunaMetrics,
    TelemetryConfig,
    TelemetryManager,
    get_telemetry_manager,
    init_telemetry,
)
from .tracing import get_tracer, trace_span


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
