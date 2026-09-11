import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session, aliased
from PIL import Image, UnidentifiedImageError

from app import models, schemas
from app.config import settings
from app.database import get_db
from routers import oauth2

router = APIRouter(prefix="/users", tags=["Profiles"])
AVATAR_DIRECTORY = settings.media_directory / "avatars"
MAX_AVATAR_BYTES = 5 * 1024 * 1024
MAX_AVATAR_DIMENSION = 4096
ALLOWED_AVATAR_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


def default_avatar_url(username: str) -> str:
    return f"https://api.dicebear.com/9.x/initials/svg?seed={quote(username)}"


def _avatar_url(user: models.User) -> str:
    return user.avatar_url if user.avatar_type == "uploaded" and user.avatar_url else default_avatar_url(user.username)


def _viewer(
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not token:
        return None
    credentials_exception = HTTPException(status_code=401, detail="Not valid credentials")
    token_data = oauth2.verify_access_token(token, credentials_exception)
    return db.query(models.User).filter(models.User.id == token_data.id).first()


def _target_user(db: Session, username: str) -> models.User:
    user = db.query(models.User).filter(func.lower(models.User.username) == username.lower()).first()
    if user is None:
        raise HTTPException(status_code=404, detail="User profile not found")
    return user


def _is_following(db: Session, follower_id: Optional[int], following_id: int) -> bool:
    if follower_id is None:
        return False
    return db.query(models.user_follows).filter(
        models.user_follows.c.follower_id == follower_id,
        models.user_follows.c.following_id == following_id,
    ).first() is not None


def can_view_profile(db: Session, viewer: Optional[models.User], target: models.User) -> bool:
    return target.profile_visibility == "public" or (viewer is not None and (
        viewer.id == target.id or _is_following(db, viewer.id, target.id)
    ))


def _require_viewable(db: Session, viewer: Optional[models.User], target: models.User) -> None:
    if not can_view_profile(db, viewer, target):
        raise HTTPException(
            status_code=403,
            detail=(
                "This profile is private. Sign in and follow this account to view it."
                if viewer is None else "This profile is private."
            ),
        )


def _safe_text(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if any(ord(character) < 32 and character not in "\n\r\t" for character in value):
        raise HTTPException(status_code=422, detail=f"{field_name} contains invalid control characters")
    if re.search(r"<\s*(script|iframe|object|embed)\b", value, re.IGNORECASE):
        raise HTTPException(status_code=422, detail=f"{field_name} contains unsupported markup")
    return value


def _profile_response(db: Session, viewer: Optional[models.User], target: models.User) -> schemas.ProfileResponse:
    followers = db.query(func.count()).select_from(models.user_follows).filter(
        models.user_follows.c.following_id == target.id,
    ).scalar() or 0
    following = db.query(func.count()).select_from(models.user_follows).filter(
        models.user_follows.c.follower_id == target.id,
    ).scalar() or 0
    posts = db.query(func.count(models.Post.id)).filter(models.Post.owner_id == target.id).scalar() or 0
    communities = db.query(func.count()).select_from(models.community_members).filter(
        models.community_members.c.user_id == target.id,
    ).scalar() or 0
    is_following = _is_following(db, viewer.id if viewer else None, target.id)
    is_followed_by = _is_following(db, target.id, viewer.id) if viewer else False
    return schemas.ProfileResponse(
        user=schemas.ProfileUser(
            id=target.id,
            username=target.username,
            display_name=target.display_name,
            bio=target.bio,
            avatar_url=_avatar_url(target),
            created_at=target.created_at,
        ),
        stats=schemas.ProfileStats(
            followers=followers,
            following=following,
            posts=posts,
            communities=communities,
        ),
        relationship=schemas.ProfileRelationship(
            is_following=is_following,
            is_followed_by=is_followed_by,
        ),
        actions=schemas.ProfileActions(
            can_follow=viewer is not None and viewer.id != target.id and not is_following,
            can_message=viewer is not None and viewer.id != target.id,
            is_self=viewer is not None and viewer.id == target.id,
        ),
        privacy=schemas.ProfilePrivacy(
            visibility=target.profile_visibility,
            show_posts=target.show_posts,
            show_communities=target.show_communities,
        ),
    )


@router.get("/{username}/profile", response_model=schemas.ProfileResponse)
def get_profile(username: str, db: Session = Depends(get_db), viewer: Optional[models.User] = Depends(_viewer)):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    return _profile_response(db, viewer, target)


@router.patch("/me/profile", response_model=schemas.UserOut)
def update_profile(
    update: schemas.ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    values = update.model_dump(exclude_unset=True)
    for field in ("display_name", "bio"):
        if field in values:
            values[field] = _safe_text(values[field], field)
    if "display_name" in values and not values["display_name"]:
        raise HTTPException(status_code=422, detail="Display name cannot be blank")
    for field, value in values.items():
        setattr(current_user, field, value)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/me/avatar", response_model=schemas.ProfileResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    data = await file.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Avatar is too large")
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
            image_format = image.format
            width, height = image.size
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=422, detail="Invalid image file")
    if image_format not in ALLOWED_AVATAR_FORMATS:
        raise HTTPException(status_code=422, detail="Only PNG, JPEG, and WebP avatars are supported")
    if width > MAX_AVATAR_DIMENSION or height > MAX_AVATAR_DIMENSION:
        raise HTTPException(status_code=422, detail="Avatar dimensions are too large")

    AVATAR_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ALLOWED_AVATAR_FORMATS[image_format]}"
    path = AVATAR_DIRECTORY / filename
    path.write_bytes(data)
    current_user.avatar_url = f"/media/avatars/{filename}"
    current_user.avatar_type = "uploaded"
    db.commit()
    db.refresh(current_user)
    return _profile_response(db, current_user, current_user)


@router.delete("/me/avatar", response_model=schemas.ProfileResponse)
def delete_avatar(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    if current_user.avatar_type == "uploaded" and current_user.avatar_url:
        path = (AVATAR_DIRECTORY / Path(current_user.avatar_url).name).resolve()
        if path.is_file() and path.parent == AVATAR_DIRECTORY.resolve():
            path.unlink()
    current_user.avatar_url = None
    current_user.avatar_type = "default"
    db.commit()
    db.refresh(current_user)
    return _profile_response(db, current_user, current_user)


def _profile_posts(db: Session, target: models.User, skip: int, limit: int):
    if not target.show_posts:
        return []
    results = db.query(models.Post, func.count(models.Vote.post_id).label("votes")).outerjoin(
        models.Vote, models.Vote.post_id == models.Post.id,
    ).filter(
        models.Post.owner_id == target.id,
        models.Post.published.is_(True),
    ).group_by(models.Post.id).order_by(
        models.Post.created_at.desc(), models.Post.id.desc(),
    ).offset(skip).limit(limit).all()
    return [{"Post": post, "votes": votes} for post, votes in results]


@router.get("/{username}/posts", response_model=list[schemas.PostOut])
def get_profile_posts(
    username: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    viewer: Optional[models.User] = Depends(_viewer),
):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    return _profile_posts(db, target, skip, limit)


@router.get("/{username}/replies", response_model=list[schemas.ReplyOut])
def get_profile_replies(
    username: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    viewer: Optional[models.User] = Depends(_viewer),
):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    if not target.show_posts:
        return []
    return db.query(models.PostReply).filter(
        models.PostReply.owner_id == target.id,
    ).order_by(models.PostReply.created_at.desc(), models.PostReply.id.desc()).offset(skip).limit(limit).all()


@router.get("/{username}/media", response_model=list[schemas.PostMediaOut])
def get_profile_media(
    username: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    viewer: Optional[models.User] = Depends(_viewer),
):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    if not target.show_posts:
        return []
    media = db.query(models.PostMedia).join(models.Post).filter(
        models.Post.owner_id == target.id,
        models.Post.published.is_(True),
    ).order_by(models.PostMedia.created_at.desc(), models.PostMedia.id.desc()).offset(skip).limit(limit).all()
    return [schemas.PostMediaOut(
        id=item.id,
        url=f"/media/{item.storage_key}",
        media_type=item.media_type,
        mime_type=item.mime_type,
        size_bytes=item.size_bytes,
        width=item.width,
        height=item.height,
        duration_seconds=item.duration_seconds,
    ) for item in media]


@router.get("/{username}/likes", response_model=list[schemas.PostOut])
def get_profile_likes(
    username: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    viewer: Optional[models.User] = Depends(_viewer),
):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    if not target.show_posts:
        return []
    vote_count = aliased(models.Vote)
    liked_vote = aliased(models.Vote)
    results = db.query(models.Post, func.count(vote_count.post_id).label("votes")).join(
        liked_vote, liked_vote.post_id == models.Post.id,
    ).filter(
        liked_vote.user_id == target.id,
        models.Post.published.is_(True),
    ).outerjoin(vote_count, vote_count.post_id == models.Post.id).group_by(
        models.Post.id,
    ).order_by(models.Post.created_at.desc(), models.Post.id.desc()).offset(skip).limit(limit).all()
    return [{"Post": post, "votes": votes} for post, votes in results]


def _profile_users(db: Session, target_id: int, following: bool, skip: int, limit: int):
    column = models.user_follows.c.following_id if following else models.user_follows.c.follower_id
    join_column = models.user_follows.c.follower_id if following else models.user_follows.c.following_id
    return db.query(models.User).join(models.user_follows, join_column == models.User.id).filter(
        column == target_id,
    ).order_by(models.user_follows.c.created_at.desc(), models.User.id.desc()).offset(skip).limit(limit).all()


@router.get("/{username}/followers", response_model=list[schemas.PublicUser])
def get_profile_followers(username: str, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), viewer: Optional[models.User] = Depends(_viewer)):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    return _profile_users(db, target.id, following=False, skip=skip, limit=limit)


@router.get("/{username}/following", response_model=list[schemas.PublicUser])
def get_profile_following(username: str, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), viewer: Optional[models.User] = Depends(_viewer)):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    return _profile_users(db, target.id, following=True, skip=skip, limit=limit)


@router.get("/{username}/communities", response_model=list[schemas.CommunityOut])
def get_profile_communities(username: str, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db), viewer: Optional[models.User] = Depends(_viewer)):
    target = _target_user(db, username)
    _require_viewable(db, viewer, target)
    if not target.show_communities:
        return []
    return db.query(models.Community).join(
        models.community_members,
        models.community_members.c.community_id == models.Community.id,
    ).filter(models.community_members.c.user_id == target.id).order_by(
        models.community_members.c.joined_at.desc(), models.Community.id.desc(),
    ).offset(skip).limit(limit).all()