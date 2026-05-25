# Handoffs

The `compact-handoff` skill writes here when invoked outside of an active slice (i.e. on `main`). Each entry is a session boundary that needs to be preserved across `/compact` or `/clear`.

Format:

```markdown
## YYYY-MM-DD HH:MM (session id or branch)
- **Doing**: <what was in progress>
- **Next**: <what comes next>
- **Open questions**: <bullets>
```
