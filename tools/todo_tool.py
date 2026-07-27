from __future__ import annotations

from core.todos import TodoStore
from .registry import ToolDef


def create_todo_tools(store: TodoStore) -> list[ToolDef]:
    async def handle_todowrite(todos: list) -> str:
        store.replace_all(todos)
        return f"Todo list updated ({len(todos)} items)"

    async def handle_todo_done(id: str) -> str:
        if store.done(id):
            return f"Todo {id} marked done"
        return f"Todo {id} not found"

    async def handle_todo_add(content: str) -> str:
        item = store.add(content)
        return f"Added todo: {content}"

    return [
        ToolDef(
            name="todowrite",
            description="Write or replace the entire todo/task list. Each item: {\"content\": str, \"status\": \"pending\"|\"done\"}.",
            parameters={
                "todos": {
                    "type": "array",
                    "description": "List of todo items",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "status": {"type": "string", "enum": ["pending", "done"]},
                        },
                    },
                },
            },
            required=["todos"],
            handler=handle_todowrite,
        ),
        ToolDef(
            name="todo_done",
            description="Mark a todo item as completed by its id.",
            parameters={
                "id": {"type": "string", "description": "Todo item id"},
            },
            required=["id"],
            handler=handle_todo_done,
        ),
        ToolDef(
            name="todo_add",
            description="Add a new todo item.",
            parameters={
                "content": {"type": "string", "description": "Todo content"},
            },
            required=["content"],
            handler=handle_todo_add,
        ),
    ]
