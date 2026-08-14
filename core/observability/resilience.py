"""
Resilience patterns for inter-service communication.
"""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable, Optional
from enum import Enum
from contextlib import asynccontextmanager

from core.observability import get_metrics, MetricNames, get_tracer, trace_span

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing, requests blocked
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5          # Failures before opening
    success_threshold: int = 2          # Successes in half-open before closing
    timeout: float = 30.0               # Seconds before half-open
    excluded_exceptions: tuple = ()     # Exceptions that don't count as failures


@dataclass
class RetryConfig:
    """Configuration for retry logic."""
    max_attempts: int = 3
    base_delay: float = 1.0             # Seconds
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: float = 0.1                 # Fraction of delay to add as jitter
    retryable_exceptions: tuple = (Exception,)


class CircuitBreaker:
    """
    Circuit breaker for protecting against cascading failures.
    
    States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Too many failures, requests fail fast
    - HALF_OPEN: Testing recovery, limited requests allowed
    """
    
    def __init__(self, name: str, config: CircuitBreakerConfig | None = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._lock = __import__("threading").RLock()
    
    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if timeout has passed to transition to half-open
                if self._last_failure_time and \
                   time.time() - self._last_failure_time >= self.config.timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
            return self._state
    
    async def call(self, func: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        if self.state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(f"Circuit breaker {self.name} is OPEN")
        
        try:
            result = await func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._success_count = 0
    
    def _on_failure(self):
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
    
    def reset(self):
        """Manually reset the circuit breaker."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


async def with_retry(
    func: Callable[..., Awaitable[Any]],
    config: RetryConfig | None = None,
    on_retry: Callable[[Exception, int], Awaitable[None]] | None = None,
) -> Any:
    """
    Execute a function with retry logic.
    
    Args:
        func: Async function to call
        config: Retry configuration
        on_retry: Optional callback called on each retry (exception, attempt_number)
    
    Returns:
        Result of the function call
    
    Raises:
        Last exception if all retries exhausted
    """
    config = config or RetryConfig()
    last_exception = None
    
    for attempt in range(config.max_attempts):
        try:
            return await func()
        except config.retryable_exceptions as e:
            last_exception = e
            
            if attempt < config.max_attempts - 1:
                delay = min(
                    config.base_delay * (config.exponential_base ** attempt),
                    config.max_delay
                )
                # Add jitter
                import random
                delay += delay * config.jitter * random.uniform(-1, 1)
                delay = max(0, delay)
                
                if on_retry:
                    await on_retry(e, attempt + 1)
                
                logger.warning(
                    f"Retry attempt {attempt + 1}/{config.max_attempts} after {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
            else:
                # Last attempt failed
                raise
    
    raise last_exception


class ResilientClient:
    """
    Wrapper for HTTP clients with circuit breaker, retry, and timeout.
    """
    
    def __init__(
        self,
        name: str,
        circuit_config: CircuitBreakerConfig | None = None,
        retry_config: RetryConfig | None = None,
        default_timeout: float = 30.0,
    ):
        self.name = name
        self.circuit_breaker = CircuitBreaker(name, circuit_config)
        self.retry_config = retry_config or RetryConfig()
        self.default_timeout = default_timeout
        self._client: Optional[Any] = None
    
    @asynccontextmanager
    async def _get_client(self):
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = __import__('aiohttp').ClientSession(
                timeout=__import__('aiohttp').ClientTimeout(total=self.default_timeout)
            )
        try:
            yield self._client
        except Exception:
            # Don't close client on error, let it be reused
            raise
    
    async def close(self):
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.close()
            self._client = None
    
    async def request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> Any:
        """Make HTTP request with resilience patterns."""
        
        async def _make_request():
            async with self._get_client() as client:
                async with client.request(method, url, **kwargs) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        
        # Apply circuit breaker
        return await self.circuit_breaker.call(
            lambda: with_retry(_make_request, self.retry_config)
        )
    
    async def get(self, url: str, **kwargs) -> Any:
        return await self.request("GET", url, **kwargs)
    
    async def post(self, url: str, **kwargs) -> Any:
        return await self.request("POST", url, **kwargs)


# Pre-configured clients for Luna and Aqua
_luna_client: Optional[ResilientClient] = None
_aqua_client: Optional[ResilientClient] = None


def get_luna_resilient_client(
    circuit_config: CircuitBreakerConfig | None = None,
    retry_config: RetryConfig | None = None,
) -> ResilientClient:
    """Get or create resilient Luna client."""
    global _luna_client
    if _luna_client is None:
        _luna_client = ResilientClient(
            "luna",
            circuit_config=circuit_config or CircuitBreakerConfig(
                failure_threshold=3,
                timeout=60.0,
            ),
            retry_config=retry_config or RetryConfig(
                max_attempts=3,
                base_delay=1.0,
            ),
            default_timeout=60.0,
        )
    return _luna_client


def get_aqua_resilient_client(
    circuit_config: CircuitBreakerConfig | None = None,
    retry_config: RetryConfig | None = None,
) -> ResilientClient:
    """Get or create resilient Aqua client."""
    global _aqua_client
    if _aqua_client is None:
        _aqua_client = ResilientClient(
            "aqua",
            circuit_config=circuit_config or CircuitBreakerConfig(
                failure_threshold=3,
                timeout=60.0,
            ),
            retry_config=retry_config or RetryConfig(
                max_attempts=3,
                base_delay=1.0,
            ),
            default_timeout=60.0,
        )
    return _aqua_client


async def delegate_to_luna(
    task: str,
    task_type: str = "code",
    context: dict | None = None,
    constraints: dict | None = None,
    stream: bool = False,
    on_event: Callable[[Any], Awaitable[None]] | None = None,
) -> Any:
    """
    Delegate a task to Luna with full resilience.
    
    This is the main entry point for Emma to delegate tasks to Luna.
    """
    from core.observability import get_metrics, MetricNames, trace_span
    
    client = get_luna_resilient_client()
    metrics = get_metrics()
    
    delegation_id = __import__('uuid').uuid4().hex[:16]
    
    with trace_span("emma", "delegation", tags={"target": "luna", "delegation_id": delegation_id}):
        metrics.increment(MetricNames.DELEGATION_STARTED, labels={"target": "luna", "type": task_type})
        
        try:
            # Build delegation payload
            payload = {
                "delegation_id": delegation_id,
                "task_type": task_type,
                "task": task,
                "context": context or {},
                "constraints": constraints or {},
                "stream": stream,
            }
            
            if stream:
                # For streaming, we'd use WebSocket - for now return acceptance
                metrics.increment(MetricNames.DELEGATION_COMPLETED, labels={"target": "luna", "mode": "ws"})
                return {"status": "accepted", "delegation_id": delegation_id}
            
            # HTTP delegation with circuit breaker and retry
            with trace_span("emma", "delegation_http", tags={"target": "luna", "delegation_id": delegation_id}):
                result = await client.post(
                    "http://localhost:8701/api/delegate",
                    json=payload,
                )
            
            metrics.increment(MetricNames.DELEGATION_COMPLETED, labels={"target": "luna"})
            return result
            
        except Exception as e:
            metrics.increment(MetricNames.DELEGATION_FAILED, labels={"target": "luna"})
            raise