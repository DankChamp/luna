"""Pytest configuration and shared fixtures for Luna tests."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def temp_workspace(temp_dir):
    """Create a temporary workspace with git repo."""
    import subprocess
    subprocess.run(["git", "init"], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=temp_dir, capture_output=True)
    # Create initial commit so git rev-parse works
    (temp_dir / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=temp_dir, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial"], cwd=temp_dir, capture_output=True)
    return temp_dir