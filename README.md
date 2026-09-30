# claude-speaks

Claude Code lit ses réponses à voix haute, avec ta voix clonée. En local sur un Mac Apple Silicon, gratuit, zéro token.

**Le tuto pas à pas : https://a-foutoyet.github.io/claude-speaks/**

Ce dépôt contient :

- `claude_speaks.py` : le script (environ 290 lignes), à placer dans `~/.claude-speaks/` ;
- `parole.md` : la commande `/parole` pour Claude Code (off, on, stop, quitter) ;
- `index.html` : la page du tuto.

Le modèle de voix est [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (Apache 2.0), via [mlx-audio](https://github.com/Blaizzy/mlx-audio) (MIT).

Licence MIT.
