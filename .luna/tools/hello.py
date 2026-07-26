from tools.registry import ToolDef


async def hello_handler(name: str = "World") -> str:
    return f"Hello, {name}! This is a custom tool."


hello_tool = ToolDef(
    name="hello",
    description="A hello world custom tool example",
    parameters={
        "name": {
            "type": "string",
            "description": "Name to greet",
        },
    },
    handler=hello_handler,
)
