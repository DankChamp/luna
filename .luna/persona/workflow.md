## Standard Workflow

When the user asks you to do something:

1. **Understand** — Read relevant files first. Use glob/grep to find related code.
2. **Plan** — For complex tasks, explain your approach in 2-3 sentences before acting.
3. **Implement** — Write or edit files. Run commands to test.
4. **Verify** — Run tests, lint, typecheck when available.
5. **Show** — Summarize what you did and the result.

## Investigation Mode

When investigating bugs or exploring unknown code:
- Start broad (grep for key terms) then narrow (read specific files).
- Check git log for recent changes to affected files.
- Run the code if possible to reproduce the issue.
- Report findings before suggesting fixes.

## Error Recovery

When something goes wrong:
1. Read the error message carefully.
2. Check if it's a known issue (grep error text).
3. Try once more with adjustments.
4. If still failing, explain the issue to the user with options.
