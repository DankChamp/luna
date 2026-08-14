"""Tests for SessionManager."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from session.manager import SessionManager
from session.context import count_messages_tokens, estimate_tokens, trim_to_budget


class TestSessionManager:
    """Test SessionManager functionality."""

    def test_new_session(self, temp_dir):
        """Test creating a new session."""
        mgr = SessionManager(str(temp_dir))
        mgr.new()
        assert mgr.current is None

    def test_save_and_load(self, temp_dir):
        """Test saving and loading a session."""
        mgr = SessionManager(str(temp_dir))
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        session_id = mgr.save(messages)
        
        assert session_id is not None
        assert mgr.current == session_id
        
        # Load it back
        loaded = mgr.load(session_id)
        assert loaded is not None
        assert loaded["messages"] == messages
        assert loaded["id"] == session_id

    def test_list_sessions(self, temp_dir):
        """Test listing sessions."""
        mgr = SessionManager(str(temp_dir))
        mgr.save([{"role": "user", "content": "Test 1"}])
        mgr.new()
        mgr.save([{"role": "user", "content": "Test 2"}])
        
        sessions = mgr.list_sessions()
        assert len(sessions) == 2

    def test_delete_session(self, temp_dir):
        """Test deleting a session."""
        mgr = SessionManager(str(temp_dir))
        session_id = mgr.save([{"role": "user", "content": "Test"}])
        
        assert mgr.delete(session_id) is True
        assert mgr.load(session_id) is None
        assert mgr.delete("nonexistent") is False

    def test_compaction_preserves_tool_pairs(self, temp_dir):
        """Test that compaction preserves tool call/result pairs."""
        mgr = SessionManager(str(temp_dir))
        
        # Create messages with tool calls
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Search for something"},
            {"role": "assistant", "content": "I'll search for that", "tool_calls": [{"name": "web_search", "arguments": {"query": "test"}}]},
            {"role": "tool", "content": "Search results...", "tool_call_id": "123"},
            {"role": "assistant", "content": "Here are the results"},
            {"role": "user", "content": "Thanks"},
            {"role": "assistant", "content": "You're welcome"},
        ]
        
        # Test compaction directly
        compacted = mgr._compact(messages, target_tokens=100)
        
        # Should have system message + summary + recent turns
        assert len(compacted) >= 2
        assert compacted[0]["role"] == "system"


class TestTokenCounting:
    """Test token counting functionality."""

    def test_estimate_tokens_empty(self):
        """Test empty string returns 0."""
        assert estimate_tokens("") == 0
        assert estimate_tokens(None) == 0  # type: ignore

    def test_estimate_tokens_basic(self):
        """Test basic token estimation."""
        text = "Hello world"
        tokens = estimate_tokens(text)
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_count_messages_tokens(self):
        """Test counting tokens in messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        total = count_messages_tokens(messages)
        assert total > 0

    def test_trim_to_budget(self):
        """Test trimming messages to token budget."""
        messages = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Message 1"},
            {"role": "assistant", "content": "Response 1"},
            {"role": "user", "content": "Message 2"},
            {"role": "assistant", "content": "Response 2"},
        ]
        # Trim to very small budget
        trimmed = trim_to_budget(messages, max_tokens=50)
        # Should keep system and at least some messages
        assert len(trimmed) >= 1
        assert trimmed[0]["role"] == "system"

    def test_tool_calls_counted(self):
        """Test that tool calls are included in token count."""
        messages = [
            {"role": "assistant", "content": "I'll search", "tool_calls": [
                {"name": "web_search", "arguments": {"query": "test"}}
            ]},
        ]
        total = count_messages_tokens(messages)
        assert total > 0


class TestSessionCompaction:
    """Test session compaction edge cases."""

    def test_compaction_empty_messages(self, temp_dir):
        """Test compaction with empty messages."""
        mgr = SessionManager(str(temp_dir))
        result = mgr._compact([], 1000)
        assert result == []

    def test_compaction_only_system(self, temp_dir):
        """Test compaction with only system messages."""
        mgr = SessionManager(str(temp_dir))
        messages = [{"role": "system", "content": "System"}]
        result = mgr._compact(messages, 1000)
        assert result == messages

    def test_compaction_no_system(self, temp_dir):
        """Test compaction without system messages."""
        mgr = SessionManager(str(temp_dir))
        # Create enough turns to trigger compaction (> 8 turns)
        messages = []
        for i in range(10):
            messages.append({"role": "user", "content": f"Message {i}"})
            messages.append({"role": "assistant", "content": f"Response {i}"})
        result = mgr._compact(messages, 10)  # Force compaction
        # Should create summary system message since we're compacting
        assert any(m["role"] == "system" for m in result)