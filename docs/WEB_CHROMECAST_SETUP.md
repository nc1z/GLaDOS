# Web UI + Chromecast Setup

Run GLaDOS with the web UI for visual feedback and audio, then cast the page to your TV so you can speak into a Bluetooth mic and hear the response from the TV.

## Prerequisites

- Bluetooth microphone (connected to your laptop)
- Chromecast (or any Cast-enabled TV)
- GLaDOS dependencies installed (`uv sync` or similar)

## Run in Two Terminals

**Terminal 1 — GLaDOS backend**

```bash
uv run glados
```

This starts the voice assistant, state server (port 7860), and audio pipeline. The Bluetooth mic on your laptop feeds speech input; TTS is streamed to connected web clients.

**Terminal 2 — Web UI**

```bash
cd web && npm run dev
```

This starts the Vite dev server at http://localhost:5173. Open that URL in Chrome.

## Simulate the Full Flow

1. **Connect Bluetooth mic to laptop** — Your laptop’s mic (including a paired Bluetooth headset) is used for speech input by GLaDOS.

2. **Start both processes** — Run `uv run glados` and `npm run dev` in separate terminals.

3. **Open the web UI** — Go to http://localhost:5173 in Chrome.

4. **Enable audio** — Click **▶ ENABLE AUDIO** (or tap anywhere) so the page can play TTS.

5. **Cast to TV** — In Chrome, use **Cast** (⋮ → Cast…) and select your Chromecast/TV. The page and its audio will stream to the TV.

6. **Speak to GLaDOS** — Either:
   - **Local mic**: Say "Jarvis, tell me a story" into the Bluetooth mic. The laptop runs ASR and the response plays through the TV.
   - **Web mic**: Use the **🎤** button on the page. Tap to start recording, tap again to send. The browser mic (e.g. on a cast device or your phone) sends audio to the backend.

## End-to-End Flow

```
Bluetooth mic (laptop)  ──►  GLaDOS (ASR)  ──►  LLM  ──►  TTS  ──►  State server  ──►  Web UI  ──►  Chromecast
Web mic (browser)        ─────────────────────────────────────────────────────────────────────────────────────►
```

- **Input**: Bluetooth mic (laptop) or web mic (browser) when using the mic button.
- **Output**: TTS is sent to the web UI via SSE; the page plays it, and Chromecast forwards audio to the TV.

## Chromecast and API URL

**Tab casting** (Cast → Cast tab): The page runs in Chrome on your laptop, so `localhost:7860` works and no changes are needed.

**Cast from another device** (e.g. open the page on your phone, then cast): The page runs on that device, so it can’t reach `localhost` on your laptop. Use your laptop’s local IP instead:

1. Find your laptop’s IP (e.g. `ifconfig` or `ip addr` → `192.168.1.5`).
2. Create `web/.env`:
   ```
   VITE_API_URL=http://192.168.1.5:7860
   ```
3. Restart `npm run dev`.

All devices must be on the same network.

## Notes

- The wake word **"Jarvis"** is required for local mic input when `wake_word` is set in config.
- Web mic input uses the same wake word and stop-command logic.
- For web mic on the cast page, use the **🎤** button; the Chromecast mic (if any) or your phone can be used depending on how you're casting.
