---
description: Voix de Claude - off (silence), on (reparle), stop (coupe la phrase), quitter (libere la memoire)
argument-hint: off | on | stop | quitter
allowed-tools: Bash(~/.claude-speaks/venv/bin/python ~/.claude-speaks/claude_speaks.py:*)
---

Lance exactement cette commande, rien d'autre :

```
~/.claude-speaks/venv/bin/python ~/.claude-speaks/claude_speaks.py $ARGUMENTS
```

Sans argument, lance-la avec `stop`.

Puis reponds en une seule ligne courte, sans rien ajouter :
- `off` : "Ok, je me tais."
- `on` : "Je reparle."
- `stop` : "Coupe."
- `quitter` : "Serveur arrete."

Si la commande renvoie une erreur, montre-la telle quelle.
