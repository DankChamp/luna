from .registry import ToolRegistry, ToolDef as ToolDef
from .read import read_tool
from .write import write_tool
from .edit import edit_tool
from .bash import bash_tool
from .glob import glob_tool
from .grep import grep_tool


def create_default_registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register_all(read_tool, write_tool, edit_tool, bash_tool, glob_tool, grep_tool)
    return r
