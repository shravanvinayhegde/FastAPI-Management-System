from datetime import datetime, timezone
import json

from fastapi import status, HTTPException, Depends, APIRouter, Query
from app import models, schemas
from app.database import get_db
from sqlalchemy.orm import Session
from routers import oauth2
from typing import Optional
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/posts", tags=["Posts"])


def _post_values(post: schemas.PostCreate) -> dict:
    values = post.model_dump()
    for field in ("image_url", "video_url"):
        if values[field] is not None:
            values[field] = str(values[field])
    return values

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=schemas.Post)
def create_posts(post: schemas.PostCreate, db: Session = Depends(get_db),
                 current_user: int = Depends(oauth2.get_current_user)):
    if post.community_id is not None:
        community = db.query(models.Community).filter(models.Community.id == post.community_id).first()
        if community is None:
            raise HTTPException(status_code=404, detail="Community not found")
        is_member = db.query(models.community_members).filter(
            models.community_members.c.community_id == post.community_id,
            models.community_members.c.user_id == current_user.id,
        ).first()
        if is_member is None:
            raise HTTPException(status_code=403, detail="Join the community before posting")
    new_post = models.Post(owner_id=current_user.id, **_post_values(post))

    db.add(new_post)
    db.commit()
    db.refresh(new_post)
    # ensure relationship is loaded before session closes
    _ = new_post.owner
    return new_post

@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_posts(id: int, db: Session = Depends(get_db),
                 current_user: int = Depends(oauth2.get_current_user)):  
    deleted_post = db.query(models.Post).filter(models.Post.id == id).first()
    
    if not deleted_post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Post with id {id} not found")
    
    if deleted_post.owner_id != current_user.id: 
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not Authorised")
    
    db.delete(deleted_post)
    db.commit()

@router.put("/{id}", status_code=status.HTTP_200_OK, response_model=schemas.Post)
def update_post(id: int, post: schemas.PostCreate, db: Session = Depends(get_db),
                current_user: int = Depends(oauth2.get_current_user)):  
    updated_post = db.query(models.Post).filter(models.Post.id == id).first()
    if not updated_post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Post with id {id} not found")
    if updated_post.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not Authorised")
    for key, value in _post_values(post).items():
        setattr(updated_post, key, value)
    db.commit()
    db.refresh(updated_post)
    # ensure relationship is loaded before session closes
    _ = updated_post.owner
    return updated_post

@router.get("/", response_model=list[schemas.PostOut]) 
def get_posts(db: Session = Depends(get_db),
              limit:int =10,
              skip:int=0,
              search: Optional[str]=""):
    results = (
        db.query(models.Post, func.count(models.Vote.post_id).label("votes"))
        .join(models.Vote, models.Vote.post_id == models.Post.id, isouter=True)
        .group_by(models.Post.id)
        .filter(models.Post.published.is_(True), models.Post.title.contains(search))
        .limit(limit)
        .offset(skip)
        .all()
    )
    # db.query(Post, votes) returns tuples, but response_model expects objects
    return [{"Post": post, "votes": votes} for post, votes in results]


@router.post("/{post_id}/share", response_model=schemas.ShareOut)
def share_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    if db.query(models.Post).filter(models.Post.id == post_id).first() is None:
        raise HTTPException(status_code=404, detail="Post not found")
    share = models.PostShare(post_id=post_id, user_id=current_user.id)
    db.add(share)
    try:
        db.commit()
        shared = True
    except IntegrityError:
        db.rollback()
        shared = False
    share_count = db.query(func.count(models.PostShare.id)).filter(
        models.PostShare.post_id == post_id,
    ).scalar() or 0
    return schemas.ShareOut(post_id=post_id, shared=shared, share_count=share_count)


@router.post("/{post_id}/replies", response_model=schemas.ReplyOut, status_code=status.HTTP_201_CREATED)
def create_reply(
    post_id: int,
    reply: schemas.ReplyCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    content = reply.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Reply content cannot be blank")
    if reply.parent_id is not None:
        parent = db.query(models.PostReply).filter(models.PostReply.id == reply.parent_id).first()
        if parent is None or parent.post_id != post_id:
            raise HTTPException(status_code=400, detail="Parent reply does not belong to this post")

    new_reply = models.PostReply(
        post_id=post_id,
        parent_id=reply.parent_id,
        owner_id=current_user.id,
        content=content,
    )
    db.add(new_reply)
    db.flush()
    notification_recipient = post.owner_id
    notification_type = "NEW_REPLY"
    if reply.parent_id is not None and parent is not None:
        notification_recipient = parent.owner_id
        notification_type = "NEW_REPLY_TO_REPLY"
    if current_user.id != notification_recipient:
        db.add(models.Notification(
            recipient_id=notification_recipient,
            actor_id=current_user.id,
            type=notification_type,
            entity_type="post_reply",
            entity_id=new_reply.id,
            payload=json.dumps({"post_id": post_id, "reply_id": new_reply.id}),
        ))
    db.commit()
    db.refresh(new_reply)
    return new_reply


@router.get("/{post_id}/replies", response_model=list[schemas.ReplyOut])
def get_replies(
    post_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if db.query(models.Post).filter(models.Post.id == post_id).first() is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return db.query(models.PostReply).filter(
        models.PostReply.post_id == post_id,
    ).order_by(models.PostReply.created_at.asc(), models.PostReply.id.asc()).offset(skip).limit(limit).all()


@router.patch("/replies/{reply_id}", response_model=schemas.ReplyOut)
def update_reply(
    reply_id: int,
    reply: schemas.ReplyCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    existing_reply = db.query(models.PostReply).filter(models.PostReply.id == reply_id).first()
    if existing_reply is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    if existing_reply.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the reply owner can edit it")
    content = reply.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="Reply content cannot be blank")
    existing_reply.content = content
    existing_reply.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(existing_reply)
    return existing_reply


@router.delete("/replies/{reply_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reply(
    reply_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    existing_reply = db.query(models.PostReply).filter(models.PostReply.id == reply_id).first()
    if existing_reply is None:
        raise HTTPException(status_code=404, detail="Reply not found")
    if existing_reply.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the reply owner can delete it")
    db.delete(existing_reply)
    db.commit()

