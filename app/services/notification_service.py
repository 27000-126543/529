from typing import Optional, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc, and_, func
from app.models import Notification, NotificationType, User
from app.redis_client import publish_message
import json


async def create_notification(
    db: AsyncSession,
    user_id: int,
    type: NotificationType,
    title: str,
    content: Optional[str] = None,
    related_id: Optional[int] = None,
    related_type: Optional[str] = None
) -> Notification:
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        content=content,
        related_id=related_id,
        related_type=related_type,
        is_read=False
    )
    db.add(notification)
    await db.flush()

    try:
        msg = json.dumps({
            "id": notification.id,
            "user_id": user_id,
            "type": type.value,
            "title": title,
            "content": content,
            "related_id": related_id,
            "related_type": related_type,
            "created_at": notification.created_at.isoformat() if notification.created_at else datetime.utcnow().isoformat()
        }, ensure_ascii=False)
        await publish_message(f"notifications:user:{user_id}", msg)
        await publish_message(f"notifications:all:{type.value}", msg)
    except Exception:
        pass

    return notification


async def create_batch_notification(
    db: AsyncSession,
    user_ids: List[int],
    type: NotificationType,
    title: str,
    content: Optional[str] = None,
    related_id: Optional[int] = None,
    related_type: Optional[str] = None
):
    notifications = []
    for uid in user_ids:
        notifications.append(Notification(
            user_id=uid,
            type=type,
            title=title,
            content=content,
            related_id=related_id,
            related_type=related_type,
            is_read=False
        ))
    db.add_all(notifications)
    await db.flush()


async def get_user_notifications(
    db: AsyncSession,
    user_id: int,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False
):
    query = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        query = query.where(Notification.is_read == False)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()

    query = query.order_by(desc(Notification.created_at)).offset((page-1)*page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()
    return total, items


async def mark_notification_read(db: AsyncSession, notification_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(Notification).where(
            and_(Notification.id == notification_id, Notification.user_id == user_id)
        )
    )
    notif = result.scalar_one_or_none()
    if notif:
        notif.is_read = True
        notif.read_at = datetime.utcnow()
        await db.flush()
        return True
    return False


async def mark_all_read(db: AsyncSession, user_id: int) -> int:
    result = await db.execute(
        select(Notification).where(
            and_(Notification.user_id == user_id, Notification.is_read == False)
        )
    )
    notifs = result.scalars().all()
    for n in notifs:
        n.is_read = True
        n.read_at = datetime.utcnow()
    await db.flush()
    return len(notifs)


async def get_unread_count(db: AsyncSession, user_id: int) -> int:
    from sqlalchemy import func
    result = await db.execute(
        select(func.count(Notification.id)).where(
            and_(Notification.user_id == user_id, Notification.is_read == False)
        )
    )
    return result.scalar_one()
