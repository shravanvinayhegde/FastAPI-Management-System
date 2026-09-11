from datetime import datetime, timezone
import json
import uuid
from io import BytesIO
from pathlib import Path

from fastapi import status, HTTPException, Depends, APIRouter, Query, File, UploadFile
from PIL import Image, UnidentifiedImageError
from app import models, schemas
from app.database import get_db
from app.config import settings
from sqlalchemy.orm import Session
from routers import oauth2
from typing import Optional
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/posts", tags=["Posts"])
POST_MEDIA_DIRECTORY = settings.media_directory / "posts"
ALLOWED_IMAGE_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}


def _post_values(post: schemas.PostCreate) -> dict:
    values = post.model_dump()
    for field in ("image_url", "video_url"):
        if values[field] is not None:
            values[field] = str(values[field])
    return values


@router.post("/media", response_model=schemas.MediaUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_post_media(file: UploadFile = File(...)):
    content_type = (file.content_type or "").lower()
    is_image = content_type.startswith("image/")
    is_video = content_type.startswith("video/")
    if not is_image and not is_video:
        raise HTTPException(status_code=422, detail="Only supported image and video files are allowed")
    limit = settings.max_image_upload_mb if is_image else settings.max_video_upload_mb
    data = await file.read(limit * 1024 * 1024 + 1)
    if len(data) > limit * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Uploaded media is too large")

    if is_image:
        try:
            with Image.open(BytesIO(data)) as image:
                image.verify()
                image_format = image.format
                width, height = image.size
        except (UnidentifiedImageError, OSError):
            raise HTTPException(status_code=422, detail="Invalid image file")
        if image_format not in ALLOWED_IMAGE_FORMATS:
            raise HTTPException(status_code=422, detail="Only PNG, JPEG, and WebP images are supported")
        if width > settings.max_image_dimension or height > settings.max_image_dimension:
            raise HTTPException(status_code=422, detail="Image dimensions are too large")
        extension = ALLOWED_IMAGE_FORMATS[image_format]
        media_type = "image"
        mime_type = f"image/{extension}" if extension != "jpg" else "image/jpeg"
    else:
        extension = Path(file.filename or "").suffix.lower()
        if extension not in ALLOWED_VIDEO_EXTENSIONS or content_type != ALLOWED_VIDEO_EXTENSIONS[extension]:
            raise HTTPException(status_code=422, detail="Only MP4, WebM, and QuickTime videos are supported")
        is_mp4_container = len(data) >= 8 and data[4:8] == b"ftyp"
        is_webm_container = data.startswith(b"\x1a\x45\xdf\xa3")
        if extension in {".mp4", ".mov"} and not is_mp4_container:
            raise HTTPException(status_code=422, detail="Invalid MP4 or QuickTime video container")
        if extension == ".webm" and not is_webm_container:
            raise HTTPException(status_code=422, detail="Invalid WebM video container")
        media_type = "video"
        mime_type = ALLOWED_VIDEO_EXTENSIONS[extension]

    filename = f"{uuid.uuid4().hex}.{extension}"
    POST_MEDIA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    (POST_MEDIA_DIRECTORY / filename).write_bytes(data)
    return schemas.MediaUploadOut(
        url=f"/media/posts/{filename}",
        media_type=media_type,
        mime_type=mime_type,
        size=len(data),
    )


@router.post("/{post_id}/media", response_model=schemas.MediaUploadOut, status_code=status.HTTP_201_CREATED)
async def attach_post_media(
    post_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the post owner can attach media")
    result = await upload_post_media(file)
    storage_key = result.url.removeprefix("/media/")
    db.add(models.PostMedia(
        post_id=post_id,
        media_type=result.media_type,
        storage_key=storage_key,
        mime_type=result.mime_type,
        size_bytes=result.size,
    ))
    db.commit()
    return result

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


@router.get("/{post_id}", response_model=schemas.PostOut)
def get_post(post_id: int, db: Session = Depends(get_db)):
    result = db.query(models.Post, func.count(models.Vote.post_id).label("votes")).outerjoin(
        models.Vote, models.Vote.post_id == models.Post.id,
    ).filter(models.Post.id == post_id, models.Post.published.is_(True)).group_by(models.Post.id).first()
    if result is None:
        raise HTTPException(status_code=404, detail="Post not found")
    post, votes = result
    return {"Post": post, "votes": votes}


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
    return schemas.ShareOut(
        post_id=post_id,
        shared=shared,
        share_count=share_count,
        url=f"{settings.frontend_url.rstrip('/')}/post/{post_id}" if settings.frontend_url else f"/post/{post_id}",
    )


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

