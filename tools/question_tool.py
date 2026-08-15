from __future__ import annotations
from typing import Optional, Any, Literal
from dataclasses import dataclass

from tools.registry import ToolDef
from core.errors import ToolError

import anyio
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.validation import Validator, ValidationError
from prompt_toolkit.completion import Completer, Completion


@dataclass
class QuestionResult:
    """Result of a question prompt."""
    answer: str
    cancelled: bool = False


class QuestionPrompter:
    """Handles interactive question prompts."""

    def __init__(self):
        self.session = PromptSession()

    async def ask_text(
        self,
        question: str,
        default: str = "",
        validate: Callable[[str], bool] | None = None,
        validate_error: str = "Invalid input",
    ) -> QuestionResult:
        """Ask a text question."""
        validator = None
        if validate:
            validator = Validator.from_callable(
                validate,
                error_message=validate_error,
                move_cursor_to_end=True,
            )

        try:
            answer = await self.session.prompt_async(
                HTML(f"<ansicyan>{question}</ansicyan> "),
                default=default,
                validator=validator,
            )
            return QuestionResult(answer=answer.strip())
        except (KeyboardInterrupt, EOFError):
            return QuestionResult(answer="", cancelled=True)

    async def ask_confirm(
        self,
        question: str,
        default: bool = True,
    ) -> QuestionResult:
        """Ask a yes/no confirmation question."""
        suffix = " [Y/n] " if default else " [y/N] "
        try:
            answer = await self.session.prompt_async(
                HTML(f"<ansicyan>{question}</ansicyan>{suffix}"),
                default="y" if default else "n",
            )
            answer = answer.strip().lower()
            if not answer:
                return QuestionResult(answer="y" if default else "n")
            return QuestionResult(answer=answer[0])
        except (KeyboardInterrupt, EOFError):
            return QuestionResult(answer="", cancelled=True)

    async def ask_choice(
        self,
        question: str,
        choices: list[str],
        default: int = 0,
    ) -> QuestionResult:
        """Ask a multiple choice question."""
        class ChoiceCompleter(Completer):
            def get_completions(self, document, complete_event):
                for i, choice in enumerate(choices):
                    yield Completion(
                        str(i + 1),
                        start_position=-len(document.text_before_cursor),
                        display=f"{i + 1}. {choice}",
                    )

        prompt_text = f"{question}\n"
        for i, choice in enumerate(choices):
            marker = "→" if i == default else " "
            prompt_text += f"  {marker} {i + 1}. {choice}\n"
        prompt_text += "Choice: "

        completer = ChoiceCompleter()

        try:
            answer = await self.session.prompt_async(
                HTML(f"<ansicyan>{prompt_text}</ansicyan>"),
                completer=completer,
                default=str(default + 1),
            )
            answer = answer.strip()
            if not answer:
                return QuestionResult(answer=str(default + 1))
            
            try:
                idx = int(answer) - 1
                if 0 <= idx < len(choices):
                    return QuestionResult(answer=str(idx + 1))
            except ValueError:
                pass
            
            # Try matching by text
            for i, choice in enumerate(choices):
                if choice.lower() == answer.lower():
                    return QuestionResult(answer=str(i + 1))
            
            return QuestionResult(answer=str(default + 1))
            
        except (KeyboardInterrupt, EOFError):
            return QuestionResult(answer="", cancelled=True)

    async def ask_multiline(
        self,
        question: str,
        default: str = "",
    ) -> QuestionResult:
        """Ask a multi-line question."""
        try:
            answer = await self.session.prompt_async(
                HTML(f"<ansicyan>{question}</ansicyan> (Ctrl+D to finish):\n"),
                default=default,
                multiline=True,
            )
            return QuestionResult(answer=answer)
        except (KeyboardInterrupt, EOFError):
            return QuestionResult(answer="", cancelled=True)


_question_prompter = QuestionPrompter()


def create_question_tool() -> ToolDef:
    """Create the question tool for interactive prompts."""

    async def question(
        prompt: str,
        type: Literal["text", "confirm", "choice", "multiline"] = "text",
        default: str | bool | int = "",
        choices: list[str] | None = None,
        validate: str | None = None,
    ) -> str:
        """
        Ask an interactive question to the user.
        
        Args:
            prompt: The question to ask
            type: Question type - 'text', 'confirm', 'choice', 'multiline'
            default: Default value (string for text/multiline, bool for confirm, int index for choice)
            choices: List of choices (for 'choice' type)
            validate: Validation regex pattern (for 'text' type)
            
        Returns:
            The user's answer
        """
        if type == "text":
            validate_func = None
            if validate:
                import re
                pattern = re.compile(validate)
                validate_func = lambda x: bool(pattern.match(x))
            
            result = await _question_prompter.ask_text(
                prompt,
                default=str(default),
                validate=validate_func,
                validate_error="Input does not match required pattern",
            )
            if result.cancelled:
                raise ToolError("Question cancelled by user", "question")
            return result.answer

        elif type == "confirm":
            default_bool = bool(default) if isinstance(default, bool) else str(default).lower() in ("true", "yes", "1", "y")
            result = await _question_prompter.ask_confirm(prompt, default_bool)
            if result.cancelled:
                raise ToolError("Question cancelled by user", "question")
            return result.answer

        elif type == "choice":
            if not choices:
                raise ToolError("Choices required for choice type", "question")
            
            default_idx = int(default) if isinstance(default, int) else 0
            result = await _question_prompter.ask_choice(prompt, choices, default_idx)
            if result.cancelled:
                raise ToolError("Question cancelled by user", "question")
            return result.answer

        elif type == "multiline":
            result = await _question_prompter.ask_multiline(prompt, str(default))
            if result.cancelled:
                raise ToolError("Question cancelled by user", "question")
            return result.answer

        else:
            raise ToolError(f"Unknown question type: {type}", "question")

    return ToolDef(
        name="question",
        description="Ask an interactive question to the user (text, confirm, choice, or multiline)",
        parameters={
            "prompt": {
                "type": "string",
                "description": "The question to ask",
            },
            "type": {
                "type": "string",
                "description": "Question type",
                "enum": ["text", "confirm", "choice", "multiline"],
                "default": "text",
            },
            "default": {
                "type": ["string", "boolean", "integer"],
                "description": "Default value",
            },
            "choices": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Choices for 'choice' type",
            },
            "validate": {
                "type": "string",
                "description": "Regex pattern for validation (text type)",
            },
        },
        required=["prompt"],
        handler=question,
    )