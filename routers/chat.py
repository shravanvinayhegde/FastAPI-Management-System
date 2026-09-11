import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from jose import JWTError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import SessionLocal, get_db
from routers import oauth2

router = APIRouter(tags=["Messaging"])


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[int, set[WebSocket]] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.setdefault(user_id, set()).add(websocket)

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        user_connections = self.connections.get(user_id)
        if not user_connections:
            return
        user_connections.discard(websocket)
        if not user_connections:
            self.connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, event: dict) -> None:
        dead_connections = []
        for websocket in self.connections.get(user_id, set()).copy():
            try:
                await websocket.send_json(event)
            except Exception:
                dead_connections.append(websocket)
        for websocket in dead_connections:
            self.disconnect(user_id, websocket)


manager = ConnectionManager()


def _get_conversation(db: Session, conversation_id: int) -> models.Conversation:
    conversation = db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


def _require_member(db: Session, conversation_id: int, user_id: int) -> models.Conversation:
    conversation = _get_conversation(db, conversation_id)
    member = db.query(models.ConversationMember).filter(
        models.ConversationMember.conversation_id == conversation_id,
        models.ConversationMember.user_id == user_id,
    ).first()
    if member is None:
        raise HTTPException(status_code=403, detail="You are not a member of this conversation")
    return conversation


def _conversation_event(message: models.Message) -> dict:
    return {
        "type": "new_message",
        "conversation_id": message.conversation_id,
        "message": schemas.MessageOut.model_validate(message).model_dump(mode="json"),
    }


def _conversation_response(db: Session, conversation: models.Conversation, user_id: int) -> schemas.ConversationOut:
    other_id = conversation.user_two_id if conversation.user_one_id == user_id else conversation.user_one_id
    other = db.query(models.User).filter(models.User.id == other_id).first()
    last_message = db.query(models.Message).filter(
        models.Message.conversation_id == conversation.id,
    ).order_by(models.Message.created_at.desc(), models.Message.id.desc()).first()
    member = db.query(models.ConversationMember).filter(
        models.ConversationMember.conversation_id == conversation.id,
        models.ConversationMember.user_id == user_id,
    ).first()
    unread_query = db.query(func.count(models.Message.id)).filter(
        models.Message.conversation_id == conversation.id,
        models.Message.sender_id != user_id,
    )
    if member and member.last_read_at is not None:
        unread_query = unread_query.filter(models.Message.created_at > member.last_read_at)
    return schemas.ConversationOut(
        id=conversation.id,
        user_one_id=conversation.user_one_id,
        user_two_id=conversation.user_two_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        other_user=schemas.PublicUser(
            id=other.id,
            username=other.username,
            display_name=other.display_name,
            avatar_url=other.avatar_url or "",
        ),
        last_message=schemas.MessageOut.model_validate(last_message) if last_message else None,
        unread_count=unread_query.scalar() or 0,
    )


@router.post("/conversations", response_model=schemas.ConversationOut, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: schemas.ConversationCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    if payload.user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Self-conversations are not supported")
    target = db.query(models.User).filter(models.User.id == payload.user_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Target user not found")

    user_one_id, user_two_id = sorted((current_user.id, payload.user_id))
    conversation = db.query(models.Conversation).filter(
        models.Conversation.user_one_id == user_one_id,
        models.Conversation.user_two_id == user_two_id,
    ).first()
    if conversation:
        return conversation

    conversation = models.Conversation(user_one_id=user_one_id, user_two_id=user_two_id)
    db.add(conversation)
    try:
        db.flush()
        db.add_all([
            models.ConversationMember(conversation_id=conversation.id, user_id=user_one_id),
            models.ConversationMember(conversation_id=conversation.id, user_id=user_two_id),
        ])
        db.commit()
    except IntegrityError:
        db.rollback()
        conversation = db.query(models.Conversation).filter(
            models.Conversation.user_one_id == user_one_id,
            models.Conversation.user_two_id == user_two_id,
        ).first()
        if conversation is None:
            raise HTTPException(status_code=409, detail="Conversation could not be created")
    else:
        db.refresh(conversation)
    return _conversation_response(db, conversation, current_user.id)


@router.get("/conversations", response_model=list[schemas.ConversationOut])
def list_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    conversations = db.query(models.Conversation).join(
        models.ConversationMember,
        models.ConversationMember.conversation_id == models.Conversation.id,
    ).filter(
        models.ConversationMember.user_id == current_user.id,
    ).order_by(models.Conversation.updated_at.desc(), models.Conversation.id.desc()).offset(skip).limit(limit).all()
    return [_conversation_response(db, conversation, current_user.id) for conversation in conversations]


@router.get("/conversations/{conversation_id}", response_model=schemas.ConversationOut)
def get_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    return _conversation_response(db, _require_member(db, conversation_id, current_user.id), current_user.id)


@router.get("/conversations/{conversation_id}/messages", response_model=list[schemas.MessageOut])
def list_messages(
    conversation_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _require_member(db, conversation_id, current_user.id)
    return db.query(models.Message).filter(
        models.Message.conversation_id == conversation_id,
    ).order_by(models.Message.created_at.asc(), models.Message.id.asc()).offset(skip).limit(limit).all()


@router.post("/conversations/{conversation_id}/messages", response_model=schemas.MessageOut, status_code=status.HTTP_201_CREATED)
async def send_message(
    conversation_id: int,
    payload: schemas.MessageCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    conversation = _require_member(db, conversation_id, current_user.id)
    content = payload.content.strip()
    if not content and payload.shared_post_id is None:
        raise HTTPException(status_code=422, detail="Message content cannot be blank")
    if payload.shared_post_id is not None and db.query(models.Post.id).filter(
        models.Post.id == payload.shared_post_id,
        models.Post.published.is_(True),
    ).first() is None:
        raise HTTPException(status_code=404, detail="Shared post not found")
    message = models.Message(
        conversation_id=conversation_id,
        sender_id=current_user.id,
        content=content,
        shared_post_id=payload.shared_post_id,
    )
    db.add(message)
    db.flush()
    conversation.updated_at = datetime.now(timezone.utc)
    recipient_id = conversation.user_two_id if conversation.user_one_id == current_user.id else conversation.user_one_id
    notification = models.Notification(
        recipient_id=recipient_id,
        actor_id=current_user.id,
        type="NEW_MESSAGE",
        entity_type="conversation",
        entity_id=conversation_id,
        payload=json.dumps({
            "message": f"{current_user.username} sent you a message",
            "conversation_id": conversation_id,
            "message_id": message.id,
            "actor_username": current_user.username,
            "message_preview": content[:120],
        }),
    )
    db.add(notification)
    db.commit()
    db.refresh(message)
    event = _conversation_event(message)
    await manager.send_to_user(recipient_id, event)
    await manager.send_to_user(current_user.id, event)
    await manager.send_to_user(recipient_id, {
        "type": "notification",
        "notification": schemas.NotificationOut.model_validate(notification).model_dump(mode="json"),
    })
    return message


@router.post("/conversations/{conversation_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_conversation_read(
    conversation_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _require_member(db, conversation_id, current_user.id)
    member = db.query(models.ConversationMember).filter(
        models.ConversationMember.conversation_id == conversation_id,
        models.ConversationMember.user_id == current_user.id,
    ).first()
    member.last_read_at = datetime.now(timezone.utc)
    db.commit()


@router.patch("/messages/{message_id}", response_model=schemas.MessageOut)
def edit_message(
    message_id: int,
    payload: schemas.MessageCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    message = db.query(models.Message).filter(models.Message.id == message_id).first()
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the sender can edit this message")
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Message content cannot be blank")
    message.content = content
    message.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(message)
    return message


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(
    message_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    message = db.query(models.Message).filter(models.Message.id == message_id).first()
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if message.sender_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the sender can delete this message")
    db.delete(message)
    db.commit()


@router.websocket("/ws/events")
async def websocket_events(websocket: WebSocket, token: Optional[str] = None):
    if not token:
        await websocket.close(code=1008)
        return
    db = SessionLocal()
    user_id: Optional[int] = None
    try:
        credentials_exception = HTTPException(status_code=401, detail="Not valid credentials")
        token_data = oauth2.verify_access_token(token, credentials_exception)
        user = db.query(models.User).filter(models.User.id == token_data.id).first()
        if user is None:
            await websocket.close(code=1008)
            return
        user_id = user.id
        await manager.connect(user_id, websocket)
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, JWTError):
        pass
    except HTTPException:
        await websocket.close(code=1008)
    finally:
        if user_id is not None:
            manager.disconnect(user_id, websocket)
        db.close()
