---
description: Claude's voice - off (silence), on (speak again), stop (cut the sentence), voice [name] (switch voice), status
argument-hint: off | on | stop | voice [name] | status
allowed-tools: Bash(claude-speaks:*)
---

Run exactly this command, nothing else:

```
claude-speaks $ARGUMENTS
```

With no argument, run `claude-speaks status`.

Then reply with one short line, nothing more:
- `off`: "Ok, going quiet."
- `on`: "Speaking again."
- `stop`: "Stopped."
- `voice` with no name: show the voice list as is.
- `voice <name>`: say which voice is now active.
- `status`: relay the status line.

If the command returns an error, show it as is and do not try to fix it.
