from datetime import datetime, timezone
import json
import logging
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app import models, schemas, storage
from app.config import settings
from app.database import get_db
from routers import oauth2

router = APIRouter(prefix="/posts", tags=["Posts"])
logger = logging.getLogger("voteflow.posts")
ALLOWED_IMAGE_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
ALLOWED_VIDEO_EXTENSIONS = {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"}


def _post_values(post: schemas.PostCreate) -> dict:
    values = post.model_dump()
    for field in ("image_url", "video_url"):
        if values[field] is not None:
            values[field] = str(values[field])
    return values


def _optional_current_user(
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not token:
        return None
    try:
        credentials_exception = HTTPException(status_code=401, detail="Not valid credentials")
        token_data = oauth2.verify_access_token(token, credentials_exception)
    except HTTPException:
        return None
    return db.query(models.User).filter(models.User.id == token_data.id).first()


def _hydrate_posts(db: Session, results: list) -> list[dict]:
    posts = [post for post, _ in results]
    if posts:
        loaded = db.query(models.Post).filter(models.Post.id.in_([p.id for p in posts])).options(
            joinedload(models.Post.owner),
            selectinload(models.Post.media),
        ).all()
        by_id = {p.id: p for p in loaded}
        results = [(by_id[post.id], votes) for post, votes in results]
    return [{"Post": post, "votes": votes} for post, votes in results]


def _attach_voted(db: Session, items: list[dict], viewer: Optional[models.User]) -> list[dict]:
    if viewer is None or not items:
        return items
    post_ids = [item["Post"].id for item in items]
    voted_ids = {
        row[0] for row in db.query(models.Vote.post_id).filter(
            models.Vote.user_id == viewer.id,
            models.Vote.post_id.in_(post_ids),
        ).all()
    }
    for item in items:
        item["voted"] = item["Post"].id in voted_ids
    return items


async def _store_post_media(file: UploadFile) -> schemas.MediaUploadOut:
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
        except (UnidentifiedImageError, OSError, getattr(Image, "DecompressionBombError", OSError)):
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

    filename = f"{uuid.uuid4().hex}.{extension.lstrip('.')}"
    try:
        await run_in_threadpool(storage.save_bytes, f"posts/{filename}", data, mime_type)
    except storage.StorageError as exc:
        logger.error(
            "Media upload failed: filename=%s content_type=%s code=%s hint=%s",
            file.filename,
            file.content_type,
            exc.code,
            exc.hint or "-",
        )
        raise HTTPException(
            status_code=503, detail=f"Media storage upload failed ({exc.code})"
        ) from exc
    return schemas.MediaUploadOut(
        url=f"/media/posts/{filename}",
        media_type=media_type,
        mime_type=mime_type,
        size=len(data),
        width=width if is_image else None,
        height=height if is_image else None,
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

    existing_media_count = db.query(func.count(models.PostMedia.id)).filter(
        models.PostMedia.post_id == post_id
    ).scalar() or 0
    if existing_media_count >= 10:
        raise HTTPException(status_code=400, detail="Maximum 10 media files per post")

    result = await _store_post_media(file)
    storage_key = result.url.removeprefix("/media/")
    try:
        db.add(models.PostMedia(
            post_id=post_id,
            media_type=result.media_type,
            storage_key=storage_key,
            mime_type=result.mime_type,
            size_bytes=result.size,
            width=result.width,
            height=result.height,
        ))
        db.commit()
    except Exception:
        db.rollback()
        storage.delete_bytes(storage_key)
        raise
    return result


@router.delete("/{post_id}/media/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post_media(
    post_id: int,
    media_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if post.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the post owner can delete media")

    media = db.query(models.PostMedia).filter(
        models.PostMedia.id == media_id,
        models.PostMedia.post_id == post_id,
    ).first()
    if media is None:
        raise HTTPException(status_code=404, detail="Media not found")

    storage.delete_bytes(media.storage_key)
    db.delete(media)
    db.commit()


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=schemas.Post)
async def create_posts(
    title: str = Form(...),
    content: str = Form(...),
    published: bool = Form(True),
    community_id: Optional[int] = Form(None),
    image: Optional[UploadFile] = File(None),
    video: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    title = title.strip()
    content = content.strip()
    if not title or not content:
        raise HTTPException(status_code=422, detail="Title and content cannot be blank")
    if community_id is not None:
        community = db.query(models.Community).filter(models.Community.id == community_id).first()
        if community is None:
            raise HTTPException(status_code=404, detail="Community not found")
        is_member = db.query(models.community_members).filter(
            models.community_members.c.community_id == community_id,
            models.community_members.c.user_id == current_user.id,
        ).first()
        if is_member is None:
            raise HTTPException(status_code=403, detail="Join the community before posting")
    stored_keys: list[str] = []
    try:
        uploaded_media = []
        for file in (image, video):
            if file is not None:
                result = await _store_post_media(file)
                uploaded_media.append(result)
                stored_keys.append(result.url.removeprefix("/media/"))

        new_post = models.Post(
            owner_id=current_user.id,
            title=title,
            content=content,
            published=published,
            community_id=community_id,
        )
        db.add(new_post)
        db.flush()
        for result in uploaded_media:
            db.add(models.PostMedia(
                post_id=new_post.id,
                media_type=result.media_type,
                storage_key=result.url.removeprefix("/media/"),
                mime_type=result.mime_type,
                size_bytes=result.size,
                width=result.width,
                height=result.height,
            ))
        db.commit()
        created_post = (
            db.query(models.Post)
            .options(
                joinedload(models.Post.owner),
                selectinload(models.Post.media),
            )
            .filter(models.Post.id == new_post.id)
            .first()
        )
        if created_post is None:
            raise RuntimeError("Created post could not be reloaded")
        return created_post
    except HTTPException:
        db.rollback()
        for key in stored_keys:
            try:
                storage.delete_bytes(key)
            except Exception:
                logger.exception(
                    "Failed to clean up uploaded media after HTTP error: key=%s",
                    key,
                )
        raise
    except Exception as exc:
        db.rollback()
        for key in stored_keys:
            try:
                storage.delete_bytes(key)
            except Exception:
                logger.exception(
                    "Failed to clean up uploaded media after database error: key=%s",
                    key,
                )
        logger.exception(
            "Failed to create post with media: user_id=%s error_type=%s error=%s",
            current_user.id,
            type(exc).__name__,
            str(exc),
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create post with media ({type(exc).__name__})",
        )


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_posts(
    id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    deleted_post = db.query(models.Post).filter(models.Post.id == id).first()
    if not deleted_post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Post with id {id} not found")
    if deleted_post.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not Authorised")
    for media in deleted_post.media:
        storage.delete_bytes(media.storage_key)
    db.delete(deleted_post)
    db.commit()


@router.put("/{id}", status_code=status.HTTP_200_OK, response_model=schemas.Post)
def update_post(
    id: int,
    post: schemas.PostUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    updated_post = db.query(models.Post).filter(models.Post.id == id).first()
    if not updated_post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Post with id {id} not found")
    if updated_post.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not Authorised")

    if post.title is not None and post.title.strip():
        updated_post.title = post.title.strip()
    if post.content is not None and post.content.strip():
        updated_post.content = post.content.strip()
    if post.published is not None:
        updated_post.published = post.published

    db.commit()
    db.refresh(updated_post)
    _ = updated_post.owner
    _ = updated_post.media
    return updated_post


@router.get("/", response_model=list[schemas.PostOut])
def get_posts(
    db: Session = Depends(get_db),
    limit: int = Query(10, ge=1, le=100),
    skip: int = Query(0, ge=0),
    search: Optional[str] = Query("", max_length=100),
    sort: str = Query("new", pattern="^(new|top|hot)$"),
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
):
    viewer = _optional_current_user(token=token, db=db)
    query = (
        db.query(models.Post, func.count(models.Vote.post_id).label("votes"))
        .outerjoin(models.Vote, models.Vote.post_id == models.Post.id)
        .filter(models.Post.published.is_(True))
        .group_by(models.Post.id)
    )
    if search and search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.filter(models.Post.title.ilike(f"%{term}%", escape="\\"))
    if sort == "top":
        query = query.order_by(func.count(models.Vote.post_id).desc(), models.Post.created_at.desc(), models.Post.id.desc())
    elif sort == "hot":
        age_hours = func.extract("epoch", func.now() - models.Post.created_at) / 3600.0
        hot_score = (func.count(models.Vote.post_id) + 1) / func.pow(age_hours + 2.0, 1.5)
        query = query.order_by(hot_score.desc(), models.Post.created_at.desc(), models.Post.id.desc())
    else:
        query = query.order_by(models.Post.created_at.desc(), models.Post.id.desc())
    results = query.offset(skip).limit(limit).all()
    items = _hydrate_posts(db, results)
    items = _attach_voted(db, items, viewer)
    return items


@router.get("/{post_id}", response_model=schemas.PostOut)
def get_post(
    post_id: int,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
):
    viewer = _optional_current_user(token=token, db=db)
    query = db.query(models.Post, func.count(models.Vote.post_id).label("votes")).outerjoin(
        models.Vote, models.Vote.post_id == models.Post.id,
    ).filter(models.Post.id == post_id)
    if viewer is None:
        query = query.filter(models.Post.published.is_(True))
    else:
        query = query.filter((models.Post.published.is_(True)) | (models.Post.owner_id == viewer.id))
    result = query.group_by(models.Post.id).first()
    if result is None:
        raise HTTPException(status_code=404, detail="Post not found")
    item = {"Post": result[0], "votes": result[1]}
    item = _attach_voted(db, [item], viewer)[0]
    return item


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
    if not post.published and post.owner_id != current_user.id:
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
            payload=json.dumps({
                "message": f"{current_user.username} replied to your post",
                "post_id": post_id,
                "reply_id": new_reply.id,
            }),
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

