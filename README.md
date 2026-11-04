# Serpantinum AI Assistant Widget

An **unofficial** community widget for [Serpantinum](https://github.com/ilyamiro/serpantinum): a chat assistant that runs on your own machine through [Ollama](https://ollama.com), with optional web search ([SearXNG](https://github.com/searxng/searxng)) and the ability to run shell commands, with approval controls.


## Versions

Both editions provide Ollama chat, model switching, an editable system prompt, local command controls, SearXNG search and page fetching, startup greetings, conversation memory, and layout-editor drag support.

### V1: Repository edition

The current repository version is the single-file `AiFace.qml` at the repository root. It provides the shared features above, but has no Python companion, voice controls, screen vision, or remote SSH tool. Follow the V1 install instructions below.

### V2: Split-backend edition

The newer edition consists of `AiFace.qml` and `AiBackend.py` in the same directory. The QML starts the Python backend and exchanges requests with it over standard input/output. V2 adds:

- Voice input using local recording and Whisper, plus spoken replies through Kokoro, Piper, or espeak-ng.
- Screen capture and vision through `grim`, either on request or with each message.
- Optional SSH access to one configured server. Remote commands always require Run/Deny approval; SSH uses strict host-key checking, public-key authentication, a non-root account, and no agent or port forwarding.
- A masked passphrase prompt that loads the key into `ssh-agent` without saving the passphrase in settings or chat.

V2 is currently the customized local copy and is not included in this repository yet. When adding it, place both files under `v2/`; do not combine the V1 QML with the V2 backend.

## Shared features

- **Local chat** with any Ollama model, streamed token by token. Click the model chip in the header to cycle through your installed models.
- **Editable system prompt** from the gear panel, saved between sessions.
- **Command tool** (`run_command`): the model can run shell commands on your machine.
  - **Off**: the model can't run anything.
  - **Ask first** (default): every command waits for your Run / Deny click.
  - **Auto-run safe**: commands that look read-only run immediately; anything that writes, deletes, installs, kills, or uses `sudo` still asks.
- **Web search** (`web_search`) through your own SearXNG instance.
- **Page fetching** (`fetch_page`): the model can open a result and read the page text.
- **Startup greeting**: a short, friendly question generated from the day, time and weather each time you boot (for example "Up for some late night gaming?"). Shown once per boot.
- **Memory**: after a chat of more than 4 of your messages, a one-line summary is saved to a plain text file and given to the model in later chats, so it can remember what you were working on.
- Stays inert while you rearrange widgets in the layout editor, so dragging works normally.

## Requirements

- A working Serpantinum setup with desktop widgets.
- [Ollama](https://ollama.com) running locally (`ollama serve`) with at least one model pulled.
- A model that supports **tool calling** if you want commands or web access. Check with `ollama show <model>` and look for `tools` under capabilities. Models without it still work for plain chat.
- A [Nerd Font](https://www.nerdfonts.com) (the widget uses icon glyphs through the shell's theme font).
- Optional: a SearXNG instance with JSON output enabled (see below).
- **V1:** `curl` and `bash`.
- **V2:** Python 3 (standard library only) and `bash` for local commands.
- **Optional V2 SSH:** OpenSSH tools (`ssh`, `ssh-add`, and `ssh-agent`).

## Install

1. Find your shell's widgets folder. It contains `WidgetRegistry.qml` and a `faces/` directory:
   ```bash
   find ~ -name WidgetRegistry.qml 2>/dev/null
   ```
2. Create `faces/ai/` next to the other faces. For V1, copy the repository's `AiFace.qml`:
   ```
   <widgets folder>/faces/ai/AiFace.qml
   ```
   The folder depth matters: the file imports `../../../reusables` and `../../../`, exactly like the shell's own faces (for example `faces/weather/WeatherFaceRound.qml`).
3. Add the widget type to the `types` object in `WidgetRegistry.qml`:
   ```qml
   "ai": {
       name: "AI Assistant",
       icon: String.fromCodePoint(0xF03B6),
       defaultWidth: 360,
       defaultHeight: 320,
       defaultVariant: "default",
       variants: {
           "default": { file: "faces/ai/AiFace.qml", icon: "1", label: "Default" }
       }
   },
   ```
4. Restart the shell, open the widget editor, and add **AI Assistant** from the toolbar.

## V2 setup

V2 is the split-backend edition in the `v2/` folder. Install `v2/AiFace.qml` and `v2/AiBackend.py` together in `faces/ai/`. Python 3 is required; no Python packages need to be installed. Quickshell starts the backend when the face loads.

### Voice

For microphone input, install either `pw-record` (PipeWire) or `arecord` (ALSA), plus `whisper-cli`/`whisper.cpp` and a Whisper `.bin` model. The default model path is `~/.local/share/whisper/ggml-base.en.bin`; set the model path in the gear panel if yours is elsewhere.

For spoken replies, choose an engine in the gear panel:

- **Kokoro:** run a local Kokoro-FastAPI server (default `http://127.0.0.1:8880`) and set its voice if needed.
- **Piper:** install Piper and a voice `.onnx` file; its matching `.onnx.json` file must be beside it.
- **espeak-ng:** used as a fallback when a local Piper voice is unavailable.

Audio playback also needs one of `pw-play`, `paplay`, or `aplay`. Recording and speech recognition run locally; the temporary recording is removed after transcription.

### Screen vision

Install `grim` and use an Ollama model that supports image input. In the gear panel, choose **When needed** or **Every message** and select a monitor scope. Screenshots are captured locally and attached to the chat request; disable screen vision when it is not needed.

### Remote SSH

1. Install OpenSSH client tools (`ssh`, `ssh-add`, and `ssh-agent`). Create a dedicated, unprivileged account on the server; the widget rejects the `root` account.
2. Connect to the server from a terminal once and verify its host-key fingerprint with the server administrator before accepting it. V2 uses strict `known_hosts` checking and will refuse an unknown or changed key.
3. Open the widget's gear panel, turn on **Remote server access (SSH)**, and enter the host or SSH alias, user, port, and optionally the private-key file path (for example `~/.ssh/id_ed25519`). Never enter or store the key's passphrase in settings.
4. Save the settings. If no usable agent key is loaded, V2 starts or reuses an `ssh-agent` and opens a masked passphrase prompt. The passphrase is passed to `ssh-add`, not saved, and the key loaded into a widget-started agent expires after 8 hours. If a desktop agent already has the key loaded, no prompt is shown.
5. When the model requests a remote command, review the target and command, then click **Run** or **Deny**. Every remote command requires approval; SSH agent and port forwarding are disabled.

## Ollama setup

```bash
ollama serve                 # if it isn't already running as a service
ollama pull qwen3.5:9b         # or any tool-capable model
```

The widget talks to `http://127.0.0.1:11434` by default. Change `ollamaHost` at the top of `AiFace.qml` if yours differs. The first run picks the first installed model; click the model chip to switch.

## SearXNG setup (optional, for web search)

SearXNG only serves HTML by default. Enable the JSON format in its `settings.yml`:

```yaml
search:
  formats:
    - html
    - json
```

If the instance is local-only, you may also need to turn off its rate limiter for API use:

```yaml
server:
  limiter: false
```

Restart SearXNG, then check it:

```bash
curl 'http://localhost:8888/search?q=test&format=json' | head -c 300
```

Open the gear panel in the widget, set the host (default `http://localhost:8888`), and press **Save**. A `403` means JSON isn't enabled; a `429` usually means the limiter is on.

## Using it

- **Enter** sends, **Shift+Enter** adds a new line, **Esc** leaves the input box.
- The widget sits on the shell's background layer, so click and type while your desktop is visible. Hover over it to give it keyboard focus.
- The **gear** opens settings: system prompt, command mode, web search on/off and host, startup greeting on/off.
- The **bin** clears the conversation. The **stop** button (shown while it works) cancels the response and any running command.
- Commands, searches and page fetches appear as cards in the chat so you can see exactly what happened.

## Memory

When a chat has more than `summaryMinReplies` messages from you (default 4), the widget asks the model for a **single short line** about what was worth remembering and appends it to `~/.local/state/ai-widget/memory.md`:

```
- 2026-10-31: Built an Ollama chat widget for Serpantinum; added SearXNG search and a startup greeting.
```

- **When does a chat count as finished?** When you press the bin (clear), after `summaryIdleMinutes` (default 10) without activity, or at the next start if the PC was shut down mid-chat. While a chat is running, the widget keeps a copy of it in `~/.local/state/ai-widget/session.json` so that last case works; the file is deleted once the chat is summarized.
- **How is it used?** The 15 most recent entries (up to 1,500 characters) are added to the system prompt, marked as possibly outdated notes to use only when relevant.
- **Short chats are never summarized**, and the model is told to skip secrets such as passwords and keys and to answer `NONE` when nothing is worth keeping.
- **It's a plain file.** Open or edit it from the gear panel (**Open file**), or delete lines yourself. **Forget all** erases it (click twice to confirm). The file is trimmed to its last 200 entries.
- Memory can be switched off in the gear panel. Summaries are written by `memoryModel` if set, otherwise `greetingModel`, otherwise the chat model, so a small model keeps this cheap.

> Privacy: the summary prompt and the session copy contain the text of your conversation, and everything stays on your machine (Ollama is local). Anyone with access to your account can read `memory.md` and `session.json`.

## Safety (please read)

This widget lets a language model run commands on your computer and read content from the internet. Treat that seriously.

- **Start in "Ask first".** Auto-run mode uses a pattern-based filter to decide what looks safe. It is a speed bump, **not a sandbox**: a creative command can slip past it.
- **Prompt injection is real.** A web page or file the model reads can contain text written to trick it into running commands. Results and pages are labelled as untrusted and the model is told to ignore instructions in them, but models can still be fooled. Keep commands on "Ask first" whenever web access is enabled, and read each command before you click Run.
- Commands run as **your user** with a 30 second timeout, no input, and truncated output. `sudo` can't work (there is no terminal), by design.
- In the **local split-backend edition**, remote SSH is off by default. It requires a configured non-root account, strict `known_hosts` verification, and public-key authentication. Every remote command requires approval; agent and port forwarding are disabled. The widget-started `ssh-agent` keeps keys for up to 8 hours. A passphrase is entered into a masked prompt and sent to `ssh-add`; it is not saved in settings or chat. Unlock your key only when you intend to use remote access.
- `fetch_page` refuses localhost and private network addresses by hostname. That check is best-effort and does not re-check redirects or DNS.
- You are responsible for what you let it run and for following the terms of any site you fetch from.

## Configuration

Most options are properties at the top of `AiFace.qml`:

| Property | Default | Meaning |
|---|---|---|
| `ollamaHost` | `http://127.0.0.1:11434` | Ollama address |
| `searxHost` | `http://localhost:8888` | SearXNG address (also editable in the gear panel) |
| `greetingModel` | `""` | Model for the startup greeting. Empty uses the chat model. Set a small model (for example `llama3.2:3b`) so boots stay fast |
| `tempUnit` | `"C"` | Unit of the shell's weather temperature (`"C"` or `"F"`), used in the greeting |
| `memoryModel` | `""` | Model used for chat summaries. Empty falls back to `greetingModel`, then the chat model |
| `summaryMinReplies` | `4` | A chat is summarized only if it has **more** than this many messages from you |
| `summaryIdleMinutes` | `10` | Minutes of inactivity before a chat is treated as finished |
| `maxMemoryEntries` / `maxMemoryChars` | `15` / `1500` | How much memory is given to the model |
| `cmdTimeout` | `30` | Seconds before a command is killed |
| `maxToolRounds` | `12` | Tool-call loops allowed per message |
| `maxOutputChars` / `maxFetchChars` | `4000` / `6000` | Output and page text sent back to the model |

Settings saved from the gear panel (prompt, command mode, model, SearXNG host, greeting, memory) are stored in `~/.local/state/ai-widget/settings.json`, and these override the defaults in the file. The last greeting and boot ID are kept in `~/.local/state/ai-widget/greeting.json`; delete it and reload the shell to force a new greeting. Memory lives in `memory.md` and `session.json` in the same folder (see [Memory](#memory)).

## Troubleshooting

- **Widget is blank:** the file is probably in the wrong folder. It must be at `faces/ai/AiFace.qml` relative to `WidgetRegistry.qml`. Check the shell's log for errors mentioning `AiFace.qml` (`qs log`, or run the shell from a terminal).
- **Can't type:** hover over the widget first. Make sure no window covers it, since it lives on the background layer.
- **"Can't reach Ollama":** start `ollama serve` and check `ollamaHost`.
- **Red error mentioning tools:** the selected model doesn't support tool calling. Pick another model, or set commands to Off and web search to Off.
- **"Search failed (is JSON format enabled?)":** see the SearXNG setup above.
- **The model uses curl instead of searching:** with web search on, `curl`/`wget` to outside URLs are refused and the model is told to use `web_search` / `fetch_page`.
- **Greeting never appears:** check that Ollama is up, the greeting is on in the gear panel, and the model can answer quickly. It retries a few times after login.


## License

Copyright (C) 2026 netcookie-12

Licensed under the **GNU Affero General Public License, version 3 or (at your option) any later version** (AGPL-3.0-or-later), the same license as Serpantinum. See [LICENSE](LICENSE).

This program comes with **no warranty**. It can run commands on your computer; you use it at your own risk.

Ollama, SearXNG, Serpantinum and any models you download are separate projects with their own licenses and are not included in this repository.
