from __future__ import annotations

from prompt_toolkit import print_formatted_text, HTML, PromptSession

from .registry import ToolDef


def create_question_tool(get_app_ref=None):
    async def _handle_question(
        question: str,
        options: list[dict],
        multiple: bool = False,
    ) -> str:
        lines = [f"<b>{question}</b>"]
        for i, opt in enumerate(options, 1):
            label = opt.get("label", f"Option {i}")
            lines.append(f"  <b>[{i}]</b> {label}")
            desc = opt.get("description", "")
            if desc:
                lines.append(f"     <i>{desc}</i>")
        lines.append("")

        app = get_app_ref() if get_app_ref else None
        markup = HTML("\n".join(lines))
        if app:
            await print_formatted_text(markup, app=app)
        else:
            print_formatted_text(markup)

        session = PromptSession()
        if multiple:
            answer = await session.prompt_async(
                "Select numbers (comma-separated, or 'none'): "
            )
        else:
            answer = await session.prompt_async("Choose (number, or 'none'): ")

        answer = answer.strip()
        if not answer or answer.lower() == "none":
            return "none"

        if multiple:
            indices = []
            for part in answer.split(","):
                part = part.strip()
                if part.isdigit():
                    idx = int(part)
                    if 1 <= idx <= len(options):
                        indices.append(idx)
            selected = [options[i - 1].get("label", f"Option {i}") for i in indices]
            return ", ".join(selected) if selected else "none"

        if answer.isdigit():
            idx = int(answer)
            if 1 <= idx <= len(options):
                return options[idx - 1].get("label", f"Option {idx}")
        return "invalid"

    return ToolDef(
        name="question",
        description=(
            "Ask the user a multiple-choice question during task execution. "
            "Use this when you need input to make a decision, choose between "
            "approaches, confirm file paths, or select options."
        ),
        parameters={
            "question": {
                "type": "string",
                "description": "The question to ask the user",
            },
            "options": {
                "type": "array",
                "description": "Answer choices — each must have a 'label' and optionally a 'description'",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {
                            "type": "string",
                            "description": "Short display text for the option",
                        },
                        "description": {
                            "type": "string",
                            "description": "Optional explanatory text",
                        },
                    },
                    "required": ["label"],
                },
            },
            "multiple": {
                "type": "boolean",
                "description": "Allow multiple selections (comma-separated). Default: false.",
            },
        },
        required=["question", "options"],
        handler=_handle_question,
    )
