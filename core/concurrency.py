from __future__ import annotations
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, TypeVar
from dataclasses import dataclass
import anyio
from anyio import create_task_group, create_memory_object_stream, CancelScope
from anyio.abc import TaskGroup

from core.errors import LunaError


T = TypeVar("T")


@dataclass
class TaskResult:
    """Result of a task execution."""
    success: bool
    value: Any = None
    error: Exception | None = None


async def run_parallel(
    tasks: list[Callable[[], AsyncIterator[T] | T]],
    max_concurrency: int | None = None,
) -> list[TaskResult]:
    """Run multiple async tasks in parallel with optional concurrency limit."""
    results: list[TaskResult] = [None] * len(tasks)

    async def run_task(index: int, task: Callable[[], AsyncIterator[T] | T]) -> None:
        try:
            if hasattr(task, "__call__"):
                result = task()
                if hasattr(result, "__aiter__"):
                    async for item in result:
                        pass
                    results[index] = TaskResult(success=True, value=item)
                else:
                    results[index] = TaskResult(success=True, value=result)
            else:
                results[index] = TaskResult(success=True, value=task)
        except Exception as e:
            results[index] = TaskResult(success=False, error=e)

    if max_concurrency is not None:
        semaphore = anyio.Semaphore(max_concurrency)

        async def limited_task(index: int, task: Callable) -> None:
            async with semaphore:
                await run_task(index, task)

        async with create_task_group() as tg:
            for i, task in enumerate(tasks):
                tg.start_soon(limited_task, i, task)
    else:
        async with create_task_group() as tg:
            for i, task in enumerate(tasks):
                tg.start_soon(run_task, i, task)

    return results


class TaskGroupManager:
    """Manages a group of related tasks with shared cancellation."""

    def __init__(self, name: str = "task-group"):
        self.name = name
        self._task_group: TaskGroup | None = None
        self._cancel_scope: CancelScope | None = None

    @asynccontextmanager
    async def create(self) -> AsyncIterator[TaskGroup]:
        """Create and manage a task group."""
        async with create_task_group() as tg:
            self._task_group = tg
            self._cancel_scope = tg.cancel_scope
            yield tg
            self._task_group = None
            self._cancel_scope = None

    def cancel(self) -> None:
        """Cancel all tasks in the group."""
        if self._cancel_scope:
            self._cancel_scope.cancel()

    @property
    def is_active(self) -> bool:
        return self._task_group is not None


async def run_with_timeout(
    coro: Callable[[], AsyncIterator[T] | T],
    timeout: float,
) -> T:
    """Run a coroutine with a timeout."""
    with anyio.fail_after(timeout):
        result = coro()
        if hasattr(result, "__aiter__"):
            async for item in result:
                return item
        return result


class StructuredConcurrency:
    """High-level structured concurrency utilities."""

    @staticmethod
    async def gather(*coros: Callable[[], AsyncIterator[T] | T]) -> list[TaskResult]:
        """Run coroutines concurrently and collect results."""
        return await run_parallel(list(coros))

    @staticmethod
    async def gather_with_limit(
        limit: int, *coros: Callable[[], AsyncIterator[T] | T]
    ) -> list[TaskResult]:
        """Run coroutines with concurrency limit."""
        return await run_parallel(list(coros), max_concurrency=limit)

    @staticmethod
    async def race(*coros: Callable[[], AsyncIterator[T] | T]) -> TaskResult:
        """Return first completed result, cancel others."""
        results: list[TaskResult] = [None] * len(coros)

        async def run_with_cancel(index: int, task: Callable) -> None:
            try:
                result = task()
                if hasattr(result, "__aiter__"):
                    async for item in result:
                        results[index] = TaskResult(success=True, value=item)
                        return
                else:
                    results[index] = TaskResult(success=True, value=result)
                    return
            except Exception as e:
                results[index] = TaskResult(success=False, error=e)

        async with create_task_group() as tg:
            for i, coro in enumerate(coros):
                tg.start_soon(run_with_cancel, i, coro)

        for result in results:
            if result is not None:
                return result

        return TaskResult(success=False, error=RuntimeError("No tasks completed"))

    @staticmethod
    @asynccontextmanager
    async def task_group(name: str = "group") -> AsyncIterator[TaskGroup]:
        """Create a named task group."""
        async with create_task_group() as tg:
            yield tg


async def with_cancellation(
    coro: Callable[[], AsyncIterator[T] | T],
    cancel_scope: CancelScope | None = None,
) -> T:
    """Run coroutine with explicit cancellation scope."""
    if cancel_scope is None:
        cancel_scope = CancelScope()

    with cancel_scope:
        result = coro()
        if hasattr(result, "__aiter__"):
            async for item in result:
                return item
        return result