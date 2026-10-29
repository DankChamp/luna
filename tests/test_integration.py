"""Integration tests for Luna agent loop with mock provider."""
from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent_core import AgentConfig, AgentCore
from core.modes import AgentMode
from core.permissions import PermissionEvaluator
from core.providers.base import (
    AIProvider,
    StreamEvent,
    TextChunk,
    ToolCall,
    ToolCallBatch,
    ToolExecEnd,
    ToolExecStart,
)
from core.tool_executor import ToolExecutor
from tools import create_default_registry


class MockProvider(AIProvider):
    """Mock AI provider for testing."""

    def __init__(self, responses: list[AsyncIterator[StreamEvent] | list[StreamEvent]]):
        self.responses = responses
        self.call_count = 0
        self.default_model = "mock-model"
        self.base_url = "http://mock"
        self.api_key = "mock-key"

    @property
    def name(self) -> str:
        return "mock/mock-model"

    async def is_available(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if self.call_count >= len(self.responses):
            # Default: return empty response
            yield TextChunk(text="Done.")
            return

        response = self.responses[self.call_count]
        self.call_count += 1

        if isinstance(response, list):
            for event in response:
                yield event
        else:
            async for event in response:
                yield event

    async def list_models(self) -> list[str]:
        return ["mock-model"]

    async def test_connection(self) -> tuple[bool, str]:
        return True, "Mock connection OK"


def create_mock_responses():
    """Create a sequence of mock provider responses."""
    # Response 1: Tool call
    tool_call = ToolCall(
        id="call_1",
        name="bash",
        arguments={"command": "echo hello"}
    )

    # Response 2: Final text
    responses = [
        [
            TextChunk(text="I'll run a command."),
            ToolCallBatch(calls=[tool_call]),
        ],
        [
            TextChunk(text="Command executed successfully."),
        ],
    ]
    return responses


class TestAgentCoreIntegration:
    """Integration tests for AgentCore with mock provider."""

    @pytest.mark.asyncio
    async def test_basic_conversation(self):
        """Test basic conversation flow."""
        responses = [
            [TextChunk(text="Hello! How can I help?")],
        ]

        provider = MockProvider(responses)

        async def provider_getter():
            return provider

        def provider_name_getter():
            return "mock/mock-model"

        tools = create_default_registry()
        permissions = PermissionEvaluator()
        tools.set_permissions(permissions)

        tool_executor = ToolExecutor(tools=tools, permissions=permissions)

        config = AgentConfig(
            max_iterations=3,
            max_history_tokens=100,
            mode=AgentMode.BUILD,
            system_prompt="You are a test assistant.",
        )

        agent = AgentCore(
            router=MagicMock(),
            tools=tools,
            config=config,
            provider_getter=provider_getter,
            get_provider_name=provider_name_getter,
        )
        agent.tools.set_executor(tool_executor)

        # Run agent
        events = []
        async for event in agent.run("Hello"):
            events.append(event)

        # Check we got a response
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) > 0
        assert "Hello" in text_events[0].text

    @pytest.mark.asyncio
    async def test_tool_execution_flow(self):
        """Test tool execution in agent loop."""
        tool_call = ToolCall(
            id="call_1",
            name="bash",
            arguments={"command": "echo hello"}
        )

        responses = [
            [
                TextChunk(text="I'll run echo."),
                ToolCallBatch(calls=[tool_call]),
            ],
            [
                TextChunk(text="Done!"),
            ],
        ]

        provider = MockProvider(responses)

        async def provider_getter():
            return provider

        def provider_name_getter():
            return "mock/mock-model"

        tools = create_default_registry()
        permissions = PermissionEvaluator()
        tools.set_permissions(permissions)

        tool_executor = ToolExecutor(tools=tools, permissions=permissions)

        config = AgentConfig(
            max_iterations=3,
            max_history_tokens=100,
            mode=AgentMode.BUILD,
            system_prompt="You are a test assistant.",
        )

        agent = AgentCore(
            router=MagicMock(),
            tools=tools,
            config=config,
            provider_getter=provider_getter,
            get_provider_name=provider_name_getter,
        )
        agent.tools.set_executor(tool_executor)

        # Run agent
        events = []
        async for event in agent.run("Run echo hello"):
            events.append(event)

        # Check we got tool execution events
        tool_starts = [e for e in events if isinstance(e, ToolExecStart)]
        tool_ends = [e for e in events if isinstance(e, ToolExecEnd)]

        assert len(tool_starts) == 1
        assert len(tool_ends) == 1
        assert tool_starts[0].name == "bash"
        assert "hello" in tool_ends[0].result

    @pytest.mark.asyncio
    async def test_max_iterations_limit(self):
        """Test that agent stops at max iterations."""
        # Create a provider that always returns tool calls
        tool_call = ToolCall(
            id="call_1",
            name="bash",
            arguments={"command": "echo test"}
        )

        responses = [
            [TextChunk(text="Working..."), ToolCallBatch(calls=[tool_call])],
            [TextChunk(text="Still working..."), ToolCallBatch(calls=[tool_call])],
            [TextChunk(text="More work..."), ToolCallBatch(calls=[tool_call])],
        ]

        provider = MockProvider(responses)

        async def provider_getter():
            return provider

        def provider_name_getter():
            return "mock/mock-model"

        tools = create_default_registry()
        permissions = PermissionEvaluator()
        tools.set_permissions(permissions)

        tool_executor = ToolExecutor(tools=tools, permissions=permissions)

        config = AgentConfig(
            max_iterations=2,  # Limit to 2 iterations
            max_history_tokens=100,
            mode=AgentMode.BUILD,
            system_prompt="You are a test assistant.",
        )

        agent = AgentCore(
            router=MagicMock(),
            tools=tools,
            config=config,
            provider_getter=provider_getter,
            get_provider_name=provider_name_getter,
        )
        agent.tools.set_executor(tool_executor)

        # Run agent
        events = []
        async for event in agent.run("Do work"):
            events.append(event)

        # Should stop at max_iterations
        # The final message should be about reaching max iterations
        final_texts = [e for e in events if isinstance(e, str) or (isinstance(e, TextChunk) and "iteration" in e.text.lower())]
        assert len(final_texts) > 0

    @pytest.mark.asyncio
    async def test_mode_switching(self):
        """Test agent mode switching affects available tools."""
        tools = create_default_registry()
        permissions = PermissionEvaluator()
        tools.set_permissions(permissions)

        config = AgentConfig(
            max_iterations=3,
            max_history_tokens=100,
            mode=AgentMode.BUILD,
            system_prompt="Test",
        )

        agent = AgentCore(
            router=MagicMock(),
            tools=tools,
            config=config,
            provider_getter=AsyncMock(),
            get_provider_name=lambda: "mock",
        )

        # In BUILD mode, all tools should be available
        build_tools = set(t.name for t in tools._tools.values() if t.name not in tools._blocked)
        assert "write" in build_tools
        assert "edit" in build_tools

        # Switch to PLAN mode
        agent.set_mode(AgentMode.PLAN)

        # In PLAN mode, write/edit/bash should be blocked
        plan_tools = set(t.name for t in tools._tools.values() if t.name not in tools._blocked)
        assert "write" not in plan_tools
        assert "edit" not in plan_tools
        assert "bash" not in plan_tools


class TestSessionIntegration:
    """Integration tests for session management."""

    @pytest.mark.asyncio
    async def test_session_save_load(self, tmp_path):
        """Test session save and load cycle."""
        from config import Settings
        from core.agent_core import AgentConfig, AgentCore
        from core.modes import AgentMode
        from core.permissions import PermissionEvaluator
        from core.session_controller import SessionController, SessionControllerConfig
        from core.tool_executor import ToolExecutor
        from session.manager import SessionManager
        from tools import create_default_registry

        settings = Settings()
        session_mgr = SessionManager(str(tmp_path / "sessions"))

        # Create agent
        tools = create_default_registry()
        permissions = PermissionEvaluator()
        tools.set_permissions(permissions)

        tool_executor = ToolExecutor(tools=tools, permissions=permissions)

        config = AgentConfig(
            max_iterations=3,
            max_history_tokens=100,
            mode=AgentMode.BUILD,
            system_prompt="Test",
        )

        # Create a mock router
        mock_router = MagicMock()
        mock_router.active_name = "mock"

        async def provider_getter():
            return AsyncMock()

        agent = AgentCore(
            router=mock_router,
            tools=tools,
            config=config,
            provider_getter=provider_getter,
            get_provider_name=lambda: "mock",
        )
        agent.tools.set_executor(tool_executor)

        # Create session controller
        session_config = SessionControllerConfig(
            session_dir=str(tmp_path / "sessions"),
            auto_save_interval=5.0,
        )
        session_controller = SessionController(agent_core=agent, config=session_config)
        await session_controller.start()

        # Add some messages
        agent.messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        # Mark session as dirty so it will be saved
        session_controller.mark_dirty()

        # Save session
        session_id = await session_controller.flush()
        assert session_id is not None

        # Create new agent and load session
        agent2 = AgentCore(
            router=mock_router,
            tools=tools,
            config=config,
            provider_getter=provider_getter,
            get_provider_name=lambda: "mock",
        )
        agent2.tools.set_executor(tool_executor)

        session_controller2 = SessionController(agent_core=agent2, config=session_config)
        await session_controller2.start()

        loaded = await session_controller2.load_session(session_id)
        assert loaded
        assert len(agent2.messages) == 2
        assert agent2.messages[0]["content"] == "Hello"

        await session_controller.stop()
        await session_controller2.stop()


class TestProviderIntegration:
    """Integration tests for provider switching."""

    @pytest.mark.asyncio
    async def test_provider_switching(self):
        """Test switching between providers."""
        from config import Settings
        from core.router import AIRouter

        settings = Settings()
        router = AIRouter(settings)

        # Test that active provider is set
        assert router.active_name in ["nvidia", "local", "openai", "anthropic"]

        # Test switching
        await router.set_active("local")
        assert router.active_name == "local"

        # Test model switching
        await router.switch_model(model="test-model")
        # Model should be updated in settings
        assert settings.local_model == "test-model"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
