from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect, APIRouter, Depends
import json
import asyncio
from app.utils.security import get_current_user
from app.models import User
from app.redis_client import get_redis
from app.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(tags=["WebSocket通知"])


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, Set[WebSocket]] = {}
        self.role_connections: Dict[str, Set[int]] = {}

    async def connect(self, websocket: WebSocket, user: User):
        await websocket.accept()
        if user.id not in self.active_connections:
            self.active_connections[user.id] = set()
        self.active_connections[user.id].add(websocket)

        role_key = user.role.value
        if role_key not in self.role_connections:
            self.role_connections[role_key] = set()
        self.role_connections[role_key].add(user.id)

        logger.info(f"WebSocket connected: user_id={user.id}, role={user.role}")

    def disconnect(self, websocket: WebSocket, user: User):
        if user.id in self.active_connections:
            self.active_connections[user.id].discard(websocket)
            if not self.active_connections[user.id]:
                del self.active_connections[user.id]
                for role in self.role_connections:
                    self.role_connections[role].discard(user.id)
        logger.info(f"WebSocket disconnected: user_id={user.id}")

    async def send_personal_message(self, message: dict, user_id: int):
        if user_id in self.active_connections:
            disconnected = []
            for ws in self.active_connections[user_id]:
                try:
                    await ws.send_text(json.dumps(message, ensure_ascii=False))
                except Exception:
                    disconnected.append(ws)
            for ws in disconnected:
                self.active_connections[user_id].discard(ws)

    async def send_to_role(self, message: dict, role: str):
        if role in self.role_connections:
            for uid in list(self.role_connections[role]):
                await self.send_personal_message(message, uid)

    async def broadcast(self, message: dict):
        for uid in list(self.active_connections.keys()):
            await self.send_personal_message(message, uid)


manager = ConnectionManager()


async def redis_listener():
    r = await get_redis()
    pubsub = r.pubsub()
    await pubsub.psubscribe("notifications:*")

    try:
        async for message in pubsub.listen():
            if message["type"] in ("pmessage", "message"):
                try:
                    channel = message.get("channel") or message.get("pattern", "")
                    data = json.loads(message["data"])
                    user_id = data.get("user_id")
                    notif_type = data.get("type")

                    if isinstance(channel, bytes):
                        channel = channel.decode("utf-8")
                    if channel.startswith("notifications:user:"):
                        uid = int(channel.split(":")[-1])
                        await manager.send_personal_message({
                            "type": "notification",
                            "data": data
                        }, uid)
                    elif channel.startswith("notifications:all:"):
                        role_map = {
                            "warning": ["dispatcher", "area_manager", "safety_inspector", "admin", "maintenance"],
                            "work_order": ["maintenance", "engineer", "dispatcher", "area_manager"],
                            "approval": ["safety_inspector", "designer", "engineer", "admin", "dispatcher"],
                            "bill": ["collector", "resident", "dispatcher"],
                            "maintenance": ["maintenance", "engineer", "dispatcher"],
                            "system": ["admin", "dispatcher"]
                        }
                        roles = role_map.get(notif_type, ["admin"])
                        for role in roles:
                            await manager.send_to_role({"type": "notification", "data": data}, role)
                except Exception as e:
                    logger.error(f"处理Redis消息失败: {e}")
    except Exception as e:
        logger.error(f"Redis listener error: {e}")
    finally:
        await pubsub.close()


@router.websocket("/ws/notifications")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str
):
    try:
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.database import AsyncSessionLocal
        from jose import JWTError, jwt
        from app.config import settings
        from sqlalchemy.future import select
        from app.models import User as DBUser

        credentials_exception = WebSocketDisconnect(code=4001, reason="Unauthorized")
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            user_id: int = payload.get("user_id")
            if user_id is None:
                await websocket.close(code=4001, reason="Invalid token")
                return
        except JWTError:
            await websocket.close(code=4001, reason="Invalid token")
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(select(DBUser).where(DBUser.id == user_id, DBUser.is_active == True))
            user = result.scalar_one_or_none()
            if not user:
                await websocket.close(code=4001, reason="User not found")
                return

        await manager.connect(websocket, user)
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    if msg.get("type") == "ping":
                        await websocket.send_text(json.dumps({"type": "pong", "timestamp": asyncio.get_event_loop().time()}))
                except Exception:
                    pass
        except WebSocketDisconnect:
            manager.disconnect(websocket, user)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close(code=4000, reason=str(e))
        except Exception:
            pass
