"""通知渠道配置管理路由"""
import json
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db_session
from api.db.crud import NotificationCRUD
from shared.core.redis_client import get_redis_client
from shared.notification.base import NotifyEvent, NotifyMessage, EVENT_LABELS

router = APIRouter()

NOTIFY_CHANNELS_KEY_PREFIX = "notify:channels:"


class ChannelCreate(BaseModel):
    channel_type: str
    name: str
    config: dict[str, Any]
    enabled_events: list[str] = []


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict[str, Any]] = None
    enabled_events: Optional[list[str]] = None
    is_active: Optional[bool] = None


class ChannelResponse(BaseModel):
    id: int
    channel_type: str
    name: str
    config: dict[str, Any]
    enabled_events: list[str]
    is_active: bool
    created_at: str


class EventInfo(BaseModel):
    value: str
    label: str


def _channel_to_response(ch) -> ChannelResponse:
    return ChannelResponse(
        id=ch.id,
        channel_type=ch.channel_type,
        name=ch.name,
        config=ch.config,
        enabled_events=ch.enabled_events or [],
        is_active=ch.is_active,
        created_at=ch.created_at.isoformat(),
    )


def _sync_channels_to_redis(user_email: str, channels) -> None:
    """将用户所有渠道配置同步到 Redis"""
    redis_client = get_redis_client()
    data = [
        {
            "channel_type": ch.channel_type,
            "name": ch.name,
            "config": ch.config,
            "enabled_events": ch.enabled_events or [],
            "is_active": ch.is_active,
        }
        for ch in channels
    ]
    redis_client.client.set(
        f"{NOTIFY_CHANNELS_KEY_PREFIX}{user_email}",
        json.dumps(data),
    )


@router.get("/events", response_model=List[EventInfo])
async def list_events():
    """获取可订阅的事件列表"""
    return [
        EventInfo(value=e.value, label=EVENT_LABELS.get(e.value, e.value))
        for e in NotifyEvent
    ]


@router.get("", response_model=List[ChannelResponse])
async def list_channels(
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    channels = await NotificationCRUD.get_all(session, user_email)
    return [_channel_to_response(ch) for ch in channels]


@router.post("", response_model=ChannelResponse, status_code=status.HTTP_201_CREATED)
async def create_channel(
    data: ChannelCreate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    valid_types = ("telegram", "dingtalk", "feishu")
    if data.channel_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的渠道类型，可选: {', '.join(valid_types)}",
        )
    channel = await NotificationCRUD.create(
        session,
        user_email=user_email,
        channel_type=data.channel_type,
        name=data.name,
        config=data.config,
        enabled_events=data.enabled_events,
    )
    # 同步到 Redis
    all_channels = await NotificationCRUD.get_all(session, user_email)
    _sync_channels_to_redis(user_email, all_channels)
    return _channel_to_response(channel)


@router.put("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: int,
    data: ChannelUpdate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    channel = await NotificationCRUD.get_by_id(session, channel_id, user_email)
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="渠道不存在")
    update_data = data.model_dump(exclude_none=True)
    channel = await NotificationCRUD.update(session, channel, **update_data)
    all_channels = await NotificationCRUD.get_all(session, user_email)
    _sync_channels_to_redis(user_email, all_channels)
    return _channel_to_response(channel)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    channel = await NotificationCRUD.get_by_id(session, channel_id, user_email)
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="渠道不存在")
    await NotificationCRUD.delete(session, channel)
    all_channels = await NotificationCRUD.get_all(session, user_email)
    _sync_channels_to_redis(user_email, all_channels)


@router.post("/{channel_id}/test")
async def test_channel(
    channel_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """发送测试通知"""
    channel = await NotificationCRUD.get_by_id(session, channel_id, user_email)
    if not channel:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="渠道不存在")

    from shared.notification.channels import CHANNEL_REGISTRY
    cls = CHANNEL_REGISTRY.get(channel.channel_type)
    if not cls:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未知渠道类型: {channel.channel_type}",
        )

    notifier = cls(**channel.config)
    msg = NotifyMessage(
        event=NotifyEvent.STRATEGY_STARTED,
        title="测试通知",
        body="如果你收到这条消息，说明通知渠道配置正确。",
        user_email=user_email,
        symbol="BTC/USDT",
    )
    success = notifier.send(msg)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="通知发送失败，请检查渠道配置",
        )
    return {"status": "ok", "message": "测试通知已发送"}
