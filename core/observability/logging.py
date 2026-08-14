"""
Structured logging with correlation IDs and context propagation.
"""
from __future__ import annotations
import logging
import json
import sys
import uuid
import contextvars
from typing import Any, Optional
from datetime import datetime
from contextlib import contextmanager

# Context variables for correlation
correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)
delegation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("delegation_id", default=None)
source_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("source", default=None)  # emma, luna, aqua


class JSONFormatter(logging.Formatter):
    """JSON log formatter with context fields."""
    
    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add context variables
        corr_id = correlation_id_var.get()
        if corr_id:
            log_data["correlation_id"] = corr_id
        
        dep_id = delegation_id_var.get()
        if dep_id:
            log_data["delegation_id"] = dep_id
        
        src = source_var.get()
        if src:
            log_data["source"] = src
        
        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in {"name", "msg", "args", "levelname", "levelno", "pathname", 
                          "filename", "module", "lineno", "funcName", "created", 
                          "msecs", "relativeCreated", "thread", "threadName", 
                          "processName", "process", "exc_info", "exc_text", "stack_info"}:
                log_data[key] = value
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_data, default=str)


class StructuredLogger:
    """Wrapper for structured logging with context."""
    
    def __init__(self, name: str, source: str = "unknown"):
        self.logger = logging.getLogger(name)
        self.source = source
    
    def _log(self, level: int, message: str, **kwargs):
        # Set context variables
        old_source = source_var.get()
        source_var.set(self.source)
        
        # Add extra fields
        extra = {k: v for k, v in kwargs.items() if not k.startswith("_")}
        
        try:
            self.logger.log(level, message, extra=extra)
        finally:
            if old_source:
                source_var.set(old_source)
            else:
                source_var.set(None)
    
    def debug(self, message: str, **kwargs):
        self._log(logging.DEBUG, message, **kwargs)
    
    def info(self, message: str, **kwargs):
        self._log(logging.INFO, message, **kwargs)
    
    def warning(self, message: str, **kwargs):
        self._log(logging.WARNING, message, **kwargs)
    
    def error(self, message: str, **kwargs):
        self._log(logging.ERROR, message, **kwargs)
    
    def critical(self, message: str, **kwargs):
        self._log(logging.CRITICAL, message, **kwargs)


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    include_source: bool = True
) -> None:
    """Configure structured logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(log_level)
    
    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        ))
    
    root_logger.addHandler(handler)
    
    # Reduce noise from third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str, source: str = "unknown") -> StructuredLogger:
    """Get a structured logger for the given module."""
    return StructuredLogger(name, source)


@contextmanager
def correlation_context(correlation_id: str | None = None):
    """Context manager for correlation ID."""
    if correlation_id is None:
        correlation_id = str(uuid.uuid4())
    
    token = correlation_id_var.set(correlation_id)
    try:
        yield correlation_id
    finally:
        correlation_id_var.reset(token)


@contextmanager
def delegation_context(delegation_id: str | None = None):
    """Context manager for delegation ID."""
    if delegation_id is None:
        delegation_id = str(uuid.uuid4())
    
    token = delegation_id_var.set(delegation_id)
    try:
        yield delegation_id
    finally:
        delegation_id_var.reset(token)


@contextmanager
def log_context(**kwargs):
    """Context manager for adding fields to all log records."""
    # This would require a custom filter or adapter
    # For now, just yield
    yield


class LogContext:
    """Context manager for structured logging with automatic correlation."""
    
    def __init__(self, operation: str, source: str, **fields):
        self.operation = operation
        self.source = source
        self.fields = fields
        self.correlation_id = str(uuid.uuid4())
        self.start_time = None
    
    def __enter__(self):
        self.start_time = __import__("time").time()
        correlation_id_var.set(self.correlation_id)
        source_var.set(self.source)
        
        logger = get_logger(f"{self.source}.{self.operation}", self.source)
        logger.info(f"Starting {self.operation}", **self.fields, correlation_id=self.correlation_id)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = __import__("time").time() - self.start_time
        
        logger = get_logger(f"{self.source}.{self.operation}", self.source)
        if exc_type:
            logger.error(
                f"Failed {self.operation}",
                duration_ms=round(duration * 1000, 2),
                error=str(exc_val),
                **self.fields
            )
        else:
            logger.info(
                f"Completed {self.operation}",
                duration_ms=round(duration * 1000, 2),
                **self.fields
            )
        correlation_id_var.set(None)
        source_var.set(None)
        return False