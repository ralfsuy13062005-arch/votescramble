import asyncio
import json
import secrets
import string
from pathlib import Path
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scramble import scramble_votes

app = FastAPI(title="Real-Time Voting & Vote Scrambler")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-Memory State
class Participant:
    def __init__(self, pid: str, name: str, is_host: bool = False):
        self.id = pid
        self.name = name
        self.is_host = is_host
        self.connected = True
        self.voted = False
        self.socket: Optional[WebSocket] = None

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "isHost": self.is_host,
            "connected": self.connected,
            "voted": self.voted,
        }

class Room:
    def __init__(self, room_id: str, host_token: str):
        self.room_id = room_id
        self.host_token = host_token
        self.status = "submitting"  # 'submitting' | 'scrambled'
        self.participants: Dict[str, Participant] = {}
        self.votes: List[Dict[str, Any]] = []
        self.assignments: Dict[str, List[Dict[str, Any]]] = {}

    def get_active_participants(self) -> List[Dict[str, Any]]:
        # Participants currently connected
        return [{"id": p.id, "name": p.name} for p in self.participants.values() if p.connected]

    def get_public_state(self):
        return {
            "roomId": self.room_id,
            "status": self.status,
            "participants": [p.to_dict() for p in self.participants.values()],
            "votesCount": len(self.votes),
            "activeCount": len(self.get_active_participants()),
        }

ROOMS: Dict[str, Room] = {}


def generate_code(length: int = 6) -> str:
    chars = string.ascii_uppercase + string.digits
    # Avoid visually ambiguous characters
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '')
    while True:
        code = ''.join(secrets.choice(chars) for _ in range(length))
        if code not in ROOMS:
            return code


class CreateRoomResponse(BaseModel):
    roomId: str
    hostToken: str


@app.post("/api/create-room", response_model=CreateRoomResponse)
async def create_room():
    room_id = generate_code(5)
    host_token = secrets.token_urlsafe(16)
    room = Room(room_id, host_token)
    ROOMS[room_id] = room
    return {"roomId": room_id, "hostToken": host_token}


@app.get("/api/room/{room_id}/info")
async def get_room_info(room_id: str):
    room_id = room_id.upper()
    if room_id not in ROOMS:
        raise HTTPException(status_code=404, detail="Room not found")
    room = ROOMS[room_id]
    return room.get_public_state()


async def broadcast_room_state(room: Room):
    state = room.get_public_state()
    for p in room.participants.values():
        if p.socket:
            try:
                # Personalize payload with their own voting & assignment status
                personal_state = {
                    "type": "room_state",
                    **state,
                    "hasVoted": p.voted,
                    "myVotes": room.assignments.get(p.id, []) if room.status == "scrambled" else [],
                }
                await p.socket.send_text(json.dumps(personal_state))
            except Exception:
                pass


@app.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    await websocket.accept()
    room_id = room_id.upper()

    if room_id not in ROOMS:
        await websocket.send_text(json.dumps({"type": "error", "message": "Room not found"}))
        await websocket.close()
        return

    room = ROOMS[room_id]
    current_participant: Optional[Participant] = None

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            action = msg.get("type")

            if action == "join":
                pid = msg.get("participantId") or secrets.token_hex(8)
                name = (msg.get("name") or "Anonymous").strip()[:30]
                host_token = msg.get("hostToken")
                is_host = (host_token is not None and host_token == room.host_token)

                if pid in room.participants:
                    # Reconnect
                    current_participant = room.participants[pid]
                    if name:
                        current_participant.name = name
                    current_participant.connected = True
                    current_participant.socket = websocket
                    if is_host:
                        current_participant.is_host = True
                else:
                    # New participant
                    current_participant = Participant(pid, name, is_host=is_host)
                    current_participant.socket = websocket
                    room.participants[pid] = current_participant

                # Confirm join to user
                await websocket.send_text(json.dumps({
                    "type": "joined",
                    "participantId": current_participant.id,
                    "name": current_participant.name,
                    "isHost": current_participant.is_host,
                    "roomId": room.room_id,
                }))

                await broadcast_room_state(room)

            elif action == "submit_vote":
                if not current_participant:
                    continue

                vote_text = (msg.get("text") or "").strip()
                if not vote_text:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Vote cannot be empty"}))
                    continue

                # Check if this participant already voted
                existing_vote = next((v for v in room.votes if v["author_id"] == current_participant.id), None)
                if existing_vote:
                    existing_vote["text"] = vote_text
                else:
                    vote_id = secrets.token_hex(6)
                    room.votes.append({
                        "id": vote_id,
                        "author_id": current_participant.id,
                        "text": vote_text,
                    })

                current_participant.voted = True
                await broadcast_room_state(room)

            elif action == "scramble":
                if not current_participant or not current_participant.is_host:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Only the host can scramble votes"}))
                    continue

                if not room.votes:
                    await websocket.send_text(json.dumps({"type": "error", "message": "No votes have been submitted yet"}))
                    continue

                active_players = room.get_active_participants()
                if not active_players:
                    await websocket.send_text(json.dumps({"type": "error", "message": "No active participants connected"}))
                    continue

                # Run scramble algorithm
                room.assignments = scramble_votes(active_players, room.votes)
                room.status = "scrambled"
                await broadcast_room_state(room)

            elif action == "new_round":
                if not current_participant or not current_participant.is_host:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Only the host can start a new round"}))
                    continue

                room.status = "submitting"
                room.votes = []
                room.assignments = {}
                for p in room.participants.values():
                    p.voted = False

                await broadcast_room_state(room)

            elif action == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        if current_participant:
            current_participant.connected = False
            current_participant.socket = None
            await broadcast_room_state(room)
    except Exception as e:
        if current_participant:
            current_participant.connected = False
            current_participant.socket = None
            await broadcast_room_state(room)


# Serve static frontend files
PUBLIC_DIR = Path(__file__).parent / "public"
if not PUBLIC_DIR.exists():
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")

@app.get("/")
async def index():
    return FileResponse(PUBLIC_DIR / "index.html")

@app.get("/{catchall:path}")
async def serve_spa(catchall: str):
    # If file exists in public dir (e.g. app.js, style.css), return it
    potential_file = PUBLIC_DIR / catchall
    if potential_file.is_file():
        return FileResponse(potential_file)
    # Otherwise fallback to index.html for SPA routing (e.g. /?room=XYZ or /room/XYZ)
    return FileResponse(PUBLIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
