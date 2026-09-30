# claude-speaks

Claude Code reads its replies out loud, in the voice you choose. Yours, if you like.

It all runs on your Mac. There is no API and no subscription, and it uses zero tokens, so your Claude plan is not touched. Once the server is warm, the first word comes out about 0.3 to 0.5 seconds after Claude finishes writing (measured on an M5 MacBook Pro).

The voice comes from [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (Apache 2.0), a voice cloning model, running on the Mac's GPU through [mlx-audio](https://github.com/Blaizzy/mlx-audio).

It reads English by default and nine other languages. It was built and tuned in French.

## How it works

1. You record 10 to 30 seconds of the voice to clone, along with the exact words you say.
2. A small local server keeps the model in memory so it can answer fast.
3. When Claude finishes a reply, a Claude Code hook takes the text, strips what doesn't read well out loud (code blocks, tables, links, emojis) and hands it over. The hook returns in a few milliseconds. The speaking happens in a separate process, and the audio starts playing while the rest is still being generated.

## Requirements

- An Apple Silicon Mac (M1 or newer). The model runs on MLX, which only exists on these chips. Windows, Linux and Intel Macs are out.
- [Claude Code](https://claude.com/claude-code).
- Python 3.10 or newer: `brew install python`.
- ffmpeg, to convert your recording: `brew install ffmpeg`.
- About 3 GB of disk space. The model weighs 2.5 GB and is downloaded once from Hugging Face.

## Install

```bash
git clone https://github.com/a-foutoyet/claude-speaks.git
cd claude-speaks
./install.sh
```

The script:

- creates a private Python environment in `~/.claude-speaks`;
- downloads the model;
- puts the `claude-speaks` command in `~/.local/bin`;
- adds the `/claude-speaks` slash command to `~/.claude/commands`.

If a file with one of those names already exists and did not come from claude-speaks, the script stops and tells you instead of overwriting it. It does not edit `settings.json`: you plug the hook in yourself, at step 3.

If the script says `~/.local/bin` is not in your PATH, add the line it prints to `~/.zshrc` and open a new terminal.

## Step 1: record your voice

The recording decides how good the voice sounds. The model copies everything it hears: your timbre, your pace, your energy, and the noise in the room too.

What works:

- **10 to 30 seconds**, one person speaking.
- **A quiet room without echo.** A closet full of clothes beats an empty living room. Background noise and reverb get cloned along with the voice, and they cannot be removed afterwards.
- **Talk the way you want Claude to talk.** The pace and energy of the sample show up in every reply. Record fast and loud, and Claude will speak fast and loud.
- **Record in the language Claude will speak.**
- **Leave the audio alone.** Noise reduction, compression and loudness normalization all alter the timbre, and the clone inherits it. Turning the volume up is fine.
- **Your phone's voice memo app is enough.** Any format ffmpeg can read works (m4a, mp3, wav, mov).

Then add the voice with a name, the file and **the exact words** you say in it, with correct punctuation:

```bash
claude-speaks voice add me my-recording.m4a "Hi, it's me. I'm testing my voice so Claude can answer me out loud."
```

Several short takes? Put them one after the other, each with its own transcript:

```bash
claude-speaks voice add me take1.m4a "Words of take one." take2.m4a "Words of take two."
```

Try it:

```bash
claude-speaks say "Hi, this is Claude. Can you hear me?"
```

The very first time, the server needs a few seconds to load the model. After that it stays in memory and answers right away.

Only clone your own voice, or the voice of someone who agreed to it.

## Step 2: pick the language

English is the default. For another language:

```bash
claude-speaks lang french
```

Available: english, french, german, italian, portuguese, spanish, japanese, korean, russian, chinese.

## Step 3: plug in the hook

```bash
claude-speaks hook install
```

This adds one entry under `hooks.Stop` in `~/.claude/settings.json`:

```json
{ "hooks": [{ "type": "command", "command": "\"/Users/you/.local/bin/claude-speaks\" hook", "timeout": 10 }] }
```

Before writing, it saves a copy of the file next to it (`settings.json.before-claude-speaks-<date>`). Every other key and every other hook stays as it was. The file is saved with standard JSON indentation, so its layout may change, not its content. If `settings.json` is a symlink (dotfiles), the file it points to is updated and the link stays. If your `settings.json` contains something unusual, such as a key written twice, it leaves the file alone and asks you to add the hook by hand.

The `Stop` hook runs when Claude finishes a reply. It reads the text Claude wrote after its last tool call, which is the final answer, and skips the short progress notes like "checking the file". If Claude Code does not pick up the hook in the current session, restart it.

To remove it: `claude-speaks hook uninstall`. It removes only its own entry.

## Commands

In the Claude Code chat:

| Command | What it does |
|---|---|
| `/claude-speaks off` | Claude goes quiet (and stops the current sentence) |
| `/claude-speaks on` | Claude speaks again |
| `/claude-speaks stop` | Stops the current sentence, automatic reading stays on |
| `/claude-speaks voice` | Lists your voices |
| `/claude-speaks voice me` | Switches voice |
| `/claude-speaks status` | Voice, model, language, server state |

The same commands work in the terminal with `claude-speaks` (no slash), plus:

| Command | What it does |
|---|---|
| `claude-speaks say "text"` | Speaks, to test |
| `claude-speaks model 1.7B` | Switches to the larger model (see below) |
| `claude-speaks lang french` | Changes the language |

## Settings

Everything lives in `~/.claude-speaks/config.json`:

```json
{
  "voice": "me",
  "model": "0.6B",
  "lang": "english",
  "auto": true,
  "temperature": 0.6
}
```

- `model`: `0.6B` by default. The `1.7B` model is available (another 4.5 GB to download). In our tests we could not hear it sound closer to the real voice, and its average pitch ranged from 138 to 172 Hz across test runs, where the 0.6B stayed between 138 and 141 Hz.
- `temperature`: how much randomness goes into the generation. mlx-audio's default is 0.9, claude-speaks uses 0.6. We measured the pitch spread across 2-second windows: 2.4 semitones when each sentence was generated on its own, 1.8 when the reply is generated in one pass (what claude-speaks does), 1.7 in one pass at 0.6. So the one-pass generation does most of the work, and the lower temperature adds a little. For reference, the original recording varies by 2.3.

After editing the file by hand, restart the server so it reads the new settings: `pkill -f "claude_speaks.py serve"`. It starts again on the next reply.

## Make Claude sound better

Claude writes for a screen. You can ask it to write for the ear instead. A few lines you can add to your `CLAUDE.md`:

```markdown
## Replies are read out loud
A text-to-speech voice reads my replies. When you can:
- keep sentences short, one idea per sentence;
- put the main point in the first two sentences;
- avoid abbreviations (write "for example", not "e.g.");
- write important numbers the way you would say them.
```

What gets dropped before reading, without asking:

- code blocks (with ``` or ~~~, even when left unclosed) and tables;
- horizontal rules and HTML tags;
- URLs (for a markdown link, the link text is read);
- inline code that looks like a path or a command, for example `~/.claude/settings.json`. Plain inline code such as `npm test` is read;
- emojis and symbols.

When the voice sounds wrong on a reply, tell Claude. Every reading is saved in `~/.claude-speaks/history/`: an audio file, plus a text file with the start time of each passage. Something like:

> The voice sounded off on your last reply, around "the server keeps a copy". Analyze the latest file in ~/.claude-speaks/history and tell me what's wrong.

That is how we found that generating one sentence at a time made the intonation change at every sentence. Claude can measure pitch, pace and silences in the file.

## Limits

- Claude speaks once it has finished writing. On a short reply you won't notice. On a long one, you wait for the end of the text.
- The first reply after a reboot waits for the model to load (a few seconds) before speaking.
- English words inside another language are sometimes pronounced with that language's accent.
- Several Claude Code sessions share one server: the last one to speak cuts off the other.
- The server keeps the model in memory while it runs. To free it: `pkill -f "claude_speaks.py serve"`.

## Privacy

Once the model is downloaded, nothing leaves your Mac: not the text of the replies, not your voice. The server listens on a local socket that only your user account can open, never on a network port. `~/.claude-speaks` (voices, reading history, config) is readable by your account only.

## Uninstall

```bash
./install.sh --uninstall
```

It removes the hook first. If that fails, it stops and tells you, rather than leave a hook pointing at a deleted program. Then it removes the commands and the program, but only the files it installed itself. Your voices, history and config stay in `~/.claude-speaks`: delete the folder if you don't want them. The model stays in the Hugging Face cache (`~/.cache/huggingface/hub`).

## Credits

- [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS), the Qwen team, Apache 2.0.
- [mlx-audio](https://github.com/Blaizzy/mlx-audio), MIT.
- The idea of a Stop hook that reads the last reply comes from [claude-read-aloud](https://github.com/MichaelPGifford/claude-read-aloud) (MIT).

MIT License.
