import asyncio
import json
import secrets
import string
from pathlib import Path
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from scramble import scramble_votes, reassign_surplus_vote

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
        active_list = self.get_active_participants()
        missing_votes = []
        surplus_count = 0
        if self.status == "scrambled":
            missing_votes = [
                {"id": p["id"], "name": p["name"]}
                for p in active_list
                if len(self.assignments.get(p["id"], [])) == 0
            ]
            surplus_count = sum(
                max(0, len(votes) - 1)
                for votes in self.assignments.values()
            )

        return {
            "roomId": self.room_id,
            "status": self.status,
            "participants": [p.to_dict() for p in self.participants.values()],
            "votesCount": len(self.votes),
            "activeCount": len(active_list),
            "missingVotes": missing_votes,
            "surplusCount": surplus_count,
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

                # Check if participant already exists by exact pid or matching offline nickname
                existing_by_pid = room.participants.get(pid)
                existing_by_name = next(
                    (p for p in room.participants.values() if p.name.strip().lower() == name.lower()),
                    None
                )

                if existing_by_pid:
                    current_participant = existing_by_pid
                    if name:
                        current_participant.name = name
                    current_participant.connected = True
                    current_participant.socket = websocket
                    if is_host:
                        current_participant.is_host = True
                elif existing_by_name:
                    if existing_by_name.connected:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": f"The name '{name}' is already currently in use by an active player."
                        }))
                        continue
                    else:
                        # Reclaim the offline account!
                        current_participant = existing_by_name
                        current_participant.connected = True
                        current_participant.socket = websocket
                        if is_host:
                            current_participant.is_host = True
                else:
                    # New participant
                    current_participant = Participant(pid, name, is_host=is_host)
                    current_participant.socket = websocket
                    room.participants[pid] = current_participant

                # Look up any existing submitted vote for this participant
                saved_vote = next((v for v in room.votes if v["author_id"] == current_participant.id), None)

                # Confirm join to user
                await websocket.send_text(json.dumps({
                    "type": "joined",
                    "participantId": current_participant.id,
                    "name": current_participant.name,
                    "isHost": current_participant.is_host,
                    "roomId": room.room_id,
                    "submittedText": saved_vote["text"] if saved_vote else "",
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

            elif action == "edit_vote":
                if current_participant and room.status == "submitting":
                    current_participant.voted = False
                    # Temporarily remove submitted vote while user is editing
                    room.votes = [v for v in room.votes if v["author_id"] != current_participant.id]
                    await broadcast_room_state(room)

            elif action in ("scramble", "rescramble_all"):
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

                # Run scramble algorithm across all active players
                room.assignments = scramble_votes(active_players, room.votes)
                room.status = "scrambled"
                await broadcast_room_state(room)

            elif action == "reassign_surplus":
                if not current_participant or not current_participant.is_host:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Only the host can reassign surplus votes"}))
                    continue

                if room.status != "scrambled":
                    await websocket.send_text(json.dumps({"type": "error", "message": "Room is not in scrambled phase"}))
                    continue

                target_pid = msg.get("targetParticipantId")
                active_players = room.get_active_participants()
                if not target_pid:
                    # Pick first active player who currently has 0 votes
                    for p in active_players:
                        if len(room.assignments.get(p["id"], [])) == 0:
                            target_pid = p["id"]
                            break

                if not target_pid:
                    await websocket.send_text(json.dumps({"type": "error", "message": "No participant needs a vote assigned"}))
                    continue

                success = reassign_surplus_vote(room.assignments, target_pid)
                if success:
                    await broadcast_room_state(room)
                else:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "message": "No available surplus vote found to transfer. Please use 'Rescramble All'."
                    }))

            elif action == "kick_participant":
                if not current_participant or not current_participant.is_host:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Only the host can kick participants"}))
                    continue

                target_pid = msg.get("targetParticipantId")
                if not target_pid or target_pid == current_participant.id:
                    await websocket.send_text(json.dumps({"type": "error", "message": "Cannot kick yourself"}))
                    continue

                if target_pid in room.participants:
                    kicked_p = room.participants[target_pid]
                    # If connected, notify them and close their socket
                    if kicked_p.socket:
                        try:
                            await kicked_p.socket.send_text(json.dumps({
                                "type": "kicked",
                                "message": f"You were removed from the room by the host."
                            }))
                            await kicked_p.socket.close()
                        except Exception:
                            pass

                    # Remove from room participants
                    del room.participants[target_pid]

                    # Remove their vote from room.votes
                    room.votes = [v for v in room.votes if v["author_id"] != target_pid]

                    # If scrambled, remove assignments for target_pid and remove any votes they authored
                    if target_pid in room.assignments:
                        del room.assignments[target_pid]
                    for pid_key, assigned_list in room.assignments.items():
                        room.assignments[pid_key] = [v for v in assigned_list if v["author_id"] != target_pid]

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


# Serve static frontend files (supports both root directory and public/ folder)
BASE_DIR = Path(__file__).parent
PUBLIC_DIR = BASE_DIR / "public"

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    error_html = f"""<!DOCTYPE html>
    <html lang="en">
    <head><meta charset="utf-8"><title>VoteScramble - Error</title></head>
    <body style="font-family:system-ui,sans-serif;padding:2.5rem;background:#0f172a;color:#f8fafc;max-width:600px;margin:auto;">
      <h2 style="color:#f43f5e;">Application Error</h2>
      <p style="background:#1e293b;padding:1rem;border-radius:0.75rem;border:1px solid #334155;font-family:monospace;font-size:0.9rem;">{str(exc)}</p>
      <a href="/" style="color:#6366f1;text-decoration:none;font-weight:600;">&larr; Return to Home</a>
    </body>
    </html>"""
    return HTMLResponse(content=error_html, status_code=500, headers={"Content-Disposition": "inline"})

def get_static_response(filename: str) -> Optional[Response]:
    clean_name = filename.lstrip("/\\")
    candidates = [
        BASE_DIR / clean_name,
        PUBLIC_DIR / clean_name
    ]
    for path in candidates:
        if path.is_file():
            content = path.read_bytes()
            media_type = "text/plain"
            if clean_name.endswith(".html") or clean_name == "":
                media_type = "text/html; charset=utf-8"
            elif clean_name.endswith(".js"):
                media_type = "application/javascript; charset=utf-8"
            elif clean_name.endswith(".css"):
                media_type = "text/css; charset=utf-8"
            return Response(
                content=content,
                media_type=media_type,
                headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"}
            )
    return None

@app.get("/")
async def index():
    res = get_static_response("index.html")
    if res:
        return res
    files = [f.name for f in BASE_DIR.iterdir()]
    return HTMLResponse(
        content=f"<h2>VoteScramble: index.html not found!</h2><p>Files in current directory: {files}</p>",
        status_code=404,
        headers={"Content-Disposition": "inline"}
    )

@app.get("/{catchall:path}")
async def serve_spa(catchall: str):
    res = get_static_response(catchall)
    if res:
        return res
    # Fallback to index.html for SPA routes
    idx_res = get_static_response("index.html")
    if idx_res:
        return idx_res
    return HTMLResponse(
        content="<h2>Page not found</h2>",
        status_code=404,
        headers={"Content-Disposition": "inline"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
