# AI Voice Assistant with OpenAI Realtime

**A production-ready AI voice assistant** built with FastAPI, Twilio Media Streams, and OpenAI’s Realtime API.

---

## 🔍 Features

- **Real-time speech recognition**: Transcribes caller audio via OpenAI Realtime (`input.text.delta`).
- **OpenAI TTS (sage)**: Streams AI-generated voice audio directly in G.711 μ-law format (`response.audio.delta`).
- **Health check endpoint**: `/health` returns `200 OK` for uptime monitoring.
- **Dashboard**: `/messages` displays call logs in a styled HTML UI.
- **Persistent logging**: All conversations logged to `messages.json`.
- **Retry logic**: Backoff-based reconnect for OpenAI WebSockets.

---

## 🛠️ Prerequisites

- Python 3.12+
- [ffmpeg](https://ffmpeg.org/) (optional for custom audio handling)
- A Twilio account with a phone number and **Media Streams** enabled
- OpenAI API key (with Realtime access)

---

## 🏗 Installation

1. Clone your private repo:
   ```bash
   git clone git@github.com:YOUR_USER/YOUR_REPO.git
   cd YOUR_REPO
   ```
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

---

## ⚙️ Environment Variables

Create a `.env` file in the project root with:

```ini
# OpenAI
OPENAI_API_KEY=sk-...
SYSTEM_MESSAGE_PATH=./prompt.txt
VOICE=sage  # change to another voice if desired

# Twilio
TWILIO_ACCOUNT_SID=AC... 
TWILIO_AUTH_TOKEN=...

# Server
PORT=5050
```

> **Note:** Do **not** commit `.env` to Git; it’s included in `.gitignore`.

---

## ▶️ Run Locally

```bash
uvicorn main:app --reload --host 0.0.0.0 --port $PORT
```

1. In Twilio Console, configure your phone number’s **Webhook** for Voice → `Incoming Call` to:
   ```
   https://<your-ngrok-or-domain>/incoming-call
   ```
2. Start ngrok (if local):
   ```bash
   ngrok http $PORT
   ```

---

## 🚀 Deployment on Railway

1. Add `railway.nix` for Python + ffmpeg (if needed).
2. Ensure Railway **Environment Variables** match local `.env`.
3. Deploy; Railway auto-detects `uvicorn main:app`.

---

## 📊 Dashboard & Logs

- Visit `GET /messages` to view caller number, timestamp, transcripts, and AI replies in a styled table.

---

## 📝 How It Works

1. **Incoming call** at `/incoming-call`: Twilio streams media to `/media-stream`.
2. **WebSocket** (`handle_media_stream`):
   - Sends session update enabling **audio** and **text** modalities, with server-side VAD and realtime transcription.
   - Streams caller audio to OpenAI.
3. **Transcription**: `input.text.delta` events are logged and shown on the dashboard.
4. **AI Reply**: OpenAI’s `response.audio.delta` streams back voice audio (sage) to Twilio in G.711 μ-law format.
5. **Error Handling**: Backoff for reconnects, graceful closure on socket `ConnectionClosedOK`.

---

## 🛠️ Troubleshooting

- **No transcription?** Ensure `modalities` includes `"text"` and `"input_audio_transcription": {"type": "realtime"}` in `send_session_update()`.
- **No AI audio?** Check Twilio Media Streams config, verify `voice` and `output_audio_format` in session.
- **500 on /messages?** Delete or reinitialize `messages.json` to a valid JSON array (`[]`).

---

## 📜 License

MIT © Syed Fahim
