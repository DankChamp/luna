from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any, Optional
from dataclasses import dataclass
import os

from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader, ConsoleMetricExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.semconv.resource import ResourceAttributes
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from opentelemetry.metrics import Counter, Histogram, UpDownCounter

from core.config_manager import ConfigManager


@dataclass
class TelemetryConfig:
    """Configuration for OpenTelemetry."""
    service_name: str = "luna"
    service_version: str = "0.1.0"
    environment: str = "development"
    otlp_endpoint: str | None = None
    enable_console_exporter: bool = False
    sample_rate: float = 1.0


class TelemetryManager:
    """Manages OpenTelemetry initialization and provides instrumentation helpers."""

    def __init__(self, config: TelemetryConfig | None = None):
        self.config = config or TelemetryConfig()
        self._tracer_provider: TracerProvider | None = None
        self._meter_provider: MeterProvider | None = None
        self._initialized = False

    def initialize(self, config_manager: ConfigManager | None = None) -> None:
        """Initialize OpenTelemetry providers."""
        if self._initialized:
            return

        # Build resource
        resource = Resource.create({
            ResourceAttributes.SERVICE_NAME: self.config.service_name,
            ResourceAttributes.SERVICE_VERSION: self.config.service_version,
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: self.config.environment,
        })

        # Initialize tracer provider
        self._tracer_provider = TracerProvider(resource=resource)

        # Add span processors
        if self.config.enable_console_exporter:
            self._tracer_provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )

        if self.config.otlp_endpoint:
            otlp_exporter = OTLPSpanExporter(endpoint=self.config.otlp_endpoint)
            self._tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

        trace.set_tracer_provider(self._tracer_provider)

        # Initialize meter provider
        readers = []
        if self.config.enable_console_exporter:
            readers.append(PeriodicExportingMetricReader(ConsoleMetricExporter()))
        if self.config.otlp_endpoint:
            readers.append(PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=self.config.otlp_endpoint)))

        if readers:
            self._meter_provider = MeterProvider(
                resource=resource,
                metric_readers=readers,
            )
            metrics.set_meter_provider(self._meter_provider)

        self._initialized = True

    def get_tracer(self, name: str) -> trace.Tracer:
        """Get a tracer for the given name."""
        if not self._initialized:
            self.initialize()
        return trace.get_tracer(name)

    def get_meter(self, name: str) -> metrics.Meter:
        """Get a meter for the given name."""
        if not self._initialized:
            self.initialize()
        return metrics.get_meter(name)

    @asynccontextmanager
    async def span(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> AsyncIterator[Span]:
        """Create a span as an async context manager."""
        tracer = self.get_tracer("luna")
        with tracer.start_as_current_span(name, kind=kind, attributes=attributes or {}) as span:
            try:
                yield span
                span.set_status(Status(StatusCode.OK))
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
                raise

    def create_counter(
        self,
        name: str,
        description: str = "",
        unit: str = "1",
    ) -> Counter:
        """Create a counter metric."""
        meter = self.get_meter("luna")
        return meter.create_counter(name, description=description, unit=unit)

    def create_histogram(
        self,
        name: str,
        description: str = "",
        unit: str = "ms",
    ) -> Histogram:
        """Create a histogram metric."""
        meter = self.get_meter("luna")
        return meter.create_histogram(name, description=description, unit=unit)

    def create_updown_counter(
        self,
        name: str,
        description: str = "",
        unit: str = "1",
    ) -> UpDownCounter:
        """Create an up-down counter metric."""
        meter = self.get_meter("luna")
        return meter.create_up_down_counter(name, description=description, unit=unit)

    def shutdown(self) -> None:
        """Shutdown telemetry providers."""
        if self._tracer_provider:
            self._tracer_provider.shutdown()
        if self._meter_provider:
            self._meter_provider.shutdown()
        self._initialized = False


# Global telemetry manager instance
_telemetry_manager: TelemetryManager | None = None


def get_telemetry_manager() -> TelemetryManager:
    """Get the global telemetry manager instance."""
    global _telemetry_manager
    if _telemetry_manager is None:
        _telemetry_manager = TelemetryManager()
    return _telemetry_manager


def init_telemetry(config: TelemetryConfig | None = None, config_manager: ConfigManager | None = None) -> TelemetryManager:
    """Initialize global telemetry."""
    global _telemetry_manager
    _telemetry_manager = TelemetryManager(config)
    _telemetry_manager.initialize(config_manager)
    return _telemetry_manager


# Pre-defined metrics for common use cases
class LunaMetrics:
    """Pre-defined metrics for Luna."""

    def __init__(self, telemetry: TelemetryManager):
        self.telemetry = telemetry

        # Agent metrics
        self.agent_runs = telemetry.create_counter(
            "luna.agent.runs.total",
            "Total number of agent runs",
        )
        self.agent_run_duration = telemetry.create_histogram(
            "luna.agent.run.duration",
            "Agent run duration",
            unit="ms",
        )
        self.agent_iterations = telemetry.create_histogram(
            "luna.agent.iterations",
            "Number of iterations per agent run",
        )

        # Tool metrics
        self.tool_calls = telemetry.create_counter(
            "luna.tool.calls.total",
            "Total number of tool calls",
        )
        self.tool_duration = telemetry.create_histogram(
            "luna.tool.duration",
            "Tool execution duration",
            unit="ms",
        )
        self.tool_errors = telemetry.create_counter(
            "luna.tool.errors.total",
            "Total number of tool errors",
        )

        # Provider metrics
        self.provider_calls = telemetry.create_counter(
            "luna.provider.calls.total",
            "Total number of provider calls",
        )
        self.provider_duration = telemetry.create_histogram(
            "luna.provider.duration",
            "Provider call duration",
            unit="ms",
        )
        self.provider_errors = telemetry.create_counter(
            "luna.provider.errors.total",
            "Total number of provider errors",
        )
        self.provider_tokens = telemetry.create_histogram(
            "luna.provider.tokens",
            "Tokens used per provider call",
        )

        # Session metrics
        self.session_duration = telemetry.create_histogram(
            "luna.session.duration",
            "Session duration",
            unit="s",
        )
        self.session_messages = telemetry.create_histogram(
            "luna.session.messages",
            "Messages per session",
        )

        # Subagent metrics
        self.subagent_runs = telemetry.create_counter(
            "luna.subagent.runs.total",
            "Total number of subagent runs",
        )
        self.subagent_duration = telemetry.create_histogram(
            "luna.subagent.duration",
            "Subagent run duration",
            unit="ms",
        )

        # Token metrics
        self.tokens_input = telemetry.create_counter(
            "luna.tokens.input.total",
            "Total input tokens",
        )
        self.tokens_output = telemetry.create_counter(
            "luna.tokens.output.total",
            "Total output tokens",
        )