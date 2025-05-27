import os
import json
import base64
import asyncio
import websockets
import openai
import requests
import backoff
from requests.auth import HTTPBasicAuth
from datetime import datetime
from fastapi import FastAPI, Request, Form, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from starlette.websockets import WebSocketDisconnect
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SYSTEM_MESSAGE_PATH = os.getenv("SYSTEM_MESSAGE_PATH", "prompt.txt")
PORT = int(os.getenv("PORT", 5050))
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
VOICE = 'sage'

app = FastAPI()
#app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

CALL_SID = None
CALLER_NUMBER = None


with open(SYSTEM_MESSAGE_PATH, 'r') as f:
    SYSTEM_MESSAGE = f.read()

LOG_EVENT_TYPES = [
    'response.content.done', 'rate_limits.updated', 'response.done',
    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started', 'response.create', 'session.created'
]
SHOW_TIMING_MATH = False

def log_message(caller: str, message: str, extra: str = ""):
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "caller": caller,
        "message": message,
        "extra": extra
    }
    file = "messages.json"
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump([entry], f)
    else:
        with open(file, "r+") as f:
            data = json.load(f)
            data.append(entry)
            f.seek(0)
            json.dump(data, f)


async def detect_end_of_call(transcript: str) -> bool:
    system_prompt = (
        "You are a call intent classifier. Your only job is to answer 'yes' or 'no'. "
        "Given a caller's last sentence, respond with 'yes' if they want to end the call, "
        "or 'no' if the conversation should continue. Do not explain."
    )

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-realtime-preview",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": transcript}
            ],
            max_tokens=1,
            temperature=0
        )
        result = response.choices[0].message.content.strip().lower()
        return result.startswith("yes")
    except Exception as e:
        print("Intent detection failed:", e)
        return False

def end_call_via_twilio():
    if not CALL_SID:
        print("No CallSid available to end call.")
        return

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Calls/{CALL_SID}.json"
    response = requests.post(url, data={"Status": "completed"}, auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))

    if response.status_code == 200:
        print("✅ Call ended successfully via Twilio.")
    else:
        print("❌ Failed to end call:", response.text)



@app.get("/health")
async def health_check():
    """
    Returns 200 if the service is healthy.
    """
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index_page():
    return "<html><body><h1>Twilio Media Stream Server is running!</h1></body></html>"

@app.post("/incoming-call")
async def incoming_call(request: Request):
    global CALL_SID, CALLER_NUMBER
    CALL_SID = form.get("CallSid")
    CALLER_NUMBER = form.get("From", "Unknown")

    form = await request.form()
    CALL_SID = form.get("CallSid")
    from_number = form.get("From", "Unknown")

    log_message(
        caller=from_number,
        message="📞 Incoming call started",
        extra=f"SID: {CALL_SID}"
    )

    host = request.url.hostname
    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    response.say("Hello! I am Fahim's assistant. Please tell me your message.")
    response.pause(length=60)
    return HTMLResponse(str(response), media_type="application/xml")

@app.get("/messages", response_class=HTMLResponse)
async def show_messages(request: Request):
    if not os.path.exists("messages.json"):
        messages = []
    else:
        with open("messages.json") as f:
            messages = json.load(f)
    return templates.TemplateResponse("dashboard.html", {"request": request, "messages": messages})

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        await send_session_update(openai_ws)

        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None

        async def receive_from_twilio():
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
                    elif data['event'] == 'stop':
                        logger.info("Twilio call ended. Closing connections.")
                        if openai_ws.open:
                            await openai_ws.close()
                            await log_websocket_status(openai_ws)
                        return
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()
        

        async def send_to_twilio():
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)


                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": audio_payload}
                        })
                        if CALLER_NUMBER and last_user_input and assistant_reply_text:
                            save_conversation(CALLER_NUMBER, last_user_input, assistant_reply_text)
                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']
                        await send_mark(websocket, stream_sid)

                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if last_assistant_item:
                            await handle_speech_started_event()
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            nonlocal response_start_timestamp_twilio, last_assistant_item
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if last_assistant_item:
                    await openai_ws.send(json.dumps({
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }))
                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })
                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                await connection.send_json({
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                })
                mark_queue.append('responsePart')

        async def log_websocket_status(ws):
            if ws.open:
                logger.info("OpenAI WebSocket is still open.")
            else:
                logger.info("OpenAI WebSocket is now closed.")

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    await openai_ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{
                "type": "input_text",
                "text": "Greet the user with 'Hello there! I am an AI voice assistant that will help you with any questions you may have.'"
            }]
        }
    }))
    await openai_ws.send(json.dumps({"type": "response.create"}))

async def send_session_update(openai_ws):
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))
    await send_initial_conversation_item(openai_ws)

@backoff.on_exception(backoff.expo, Exception, max_tries=3)
async def connect_openai_ws():
    return await websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    )

def save_conversation(caller: str, user_msg: str, ai_reply: str):
    file = f"memory/{caller}.json"
    os.makedirs("memory", exist_ok=True)
    entry = {
        "time": datetime.now().isoformat(),
        "user": user_msg,
        "ai": ai_reply
    }
    if not os.path.exists(file):
        with open(file, "w") as f:
            json.dump([entry], f)
    else:
        with open(file, "r+") as f:
            history = json.load(f)
            history.append(entry)
            f.seek(0)
            json.dump(history, f)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
