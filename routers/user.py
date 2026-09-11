import re
import json

from fastapi import status, HTTPException, Depends, APIRouter, Query, BackgroundTasks
from app import models, schemas, utility
from app.database import get_db
from sqlalchemy import delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from routers import oauth2
from routers.chat import manager

router = APIRouter(prefix="/users", tags=["Users"])

@router.get("/", response_model=list[schemas.UserOut])
def get_all_users(
    db: Session = Depends(get_db),
    current_user: int = Depends(oauth2.get_current_user)  # added auth
):
    return db.query(models.User).all()

@router.post("/", status_code=status.HTTP_201_CREATED, response_model=schemas.UserOut)
def create_user(user: schemas.UserCreate, db: Session = Depends(get_db)):
    hashed_password = utility.hash(user.password)  # cleaner utility call
    requested_username = user.username or user.email.split("@", 1)[0]
    username = re.sub(r"[^a-zA-Z0-9_]+", "_", requested_username).strip("_").lower()[:50]
    if len(username) < 3:
        username = f"user_{len(user.email)}"
    base_username = username
    suffix = 1
    while db.query(models.User).filter(models.User.username == username).first():
        suffix += 1
        username = f"{base_username[:50 - len(str(suffix)) - 1]}_{suffix}"
    new_user = models.User(
        username=username,
        email=user.email,
        password=hashed_password,
        display_name=username,
    )
    db.add(new_user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email or username is already registered")
    db.refresh(new_user)
    return new_user

@router.get("/{id}", response_model=schemas.UserOut)
def get_user(
    id: int,
    db: Session = Depends(get_db),
    current_user: int = Depends(oauth2.get_current_user)
):
    user = db.query(models.User).filter(models.User.id == id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"User with id {id} does not exist")
    if user.profile_visibility == "private" and current_user.id != user.id:
        follows = db.query(models.user_follows).filter(
            models.user_follows.c.follower_id == current_user.id,
            models.user_follows.c.following_id == user.id,
        ).first()
        if follows is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This profile is private")
    return user


@router.get("/me/communities", response_model=list[schemas.CommunityOut])
def get_my_communities(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    return db.query(models.Community).join(
        models.community_members,
        models.community_members.c.community_id == models.Community.id,
    ).filter(
        models.community_members.c.user_id == current_user.id,
    ).order_by(models.community_members.c.joined_at.desc()).offset(skip).limit(limit).all()


def _get_follow_status(db: Session, current_user_id: int, target_user_id: int) -> schemas.FollowStatus:
    following = db.query(models.user_follows).filter(
        models.user_follows.c.follower_id == current_user_id,
        models.user_follows.c.following_id == target_user_id,
    ).first() is not None
    follower_count = db.query(func.count()).select_from(models.user_follows).filter(
        models.user_follows.c.following_id == target_user_id,
    ).scalar() or 0
    following_count = db.query(func.count()).select_from(models.user_follows).filter(
        models.user_follows.c.follower_id == target_user_id,
    ).scalar() or 0
    return schemas.FollowStatus(
        following=following,
        follower_count=follower_count,
        following_count=following_count,
    )


def _get_target_user(db: Session, user_id: int) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with id {user_id} does not exist",
        )
    return user


@router.post("/{id}/follow", response_model=schemas.FollowStatus)
def follow_user(
    id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _get_target_user(db, id)
    if current_user.id == id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Users cannot follow themselves",
        )

    try:
        db.execute(models.user_follows.insert().values(
            follower_id=current_user.id,
            following_id=id,
        ))
        notification = models.Notification(
            recipient_id=id,
            actor_id=current_user.id,
            type="NEW_FOLLOWER",
            entity_type="user",
            entity_id=current_user.id,
            payload=json.dumps({"follower_id": current_user.id}),
        )
        db.add(notification)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already followed",
        )
    db.refresh(notification)
    background_tasks.add_task(manager.send_to_user, id, {
        "type": "notification",
        "notification": schemas.NotificationOut.model_validate(notification).model_dump(mode="json"),
    })
    return _get_follow_status(db, current_user.id, id)


@router.delete("/{id}/follow", response_model=schemas.FollowStatus)
def unfollow_user(
    id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _get_target_user(db, id)
    result = db.execute(delete(models.user_follows).where(
        models.user_follows.c.follower_id == current_user.id,
        models.user_follows.c.following_id == id,
    ))
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Follow relationship does not exist",
        )
    db.commit()
    return _get_follow_status(db, current_user.id, id)


@router.get("/{id}/follow-status", response_model=schemas.FollowStatus)
def get_follow_status(
    id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _get_target_user(db, id)
    return _get_follow_status(db, current_user.id, id)


@router.get("/{id}/followers", response_model=list[schemas.UserOut])
def get_followers(
    id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _get_target_user(db, id)
    return db.query(models.User).join(
        models.user_follows,
        models.user_follows.c.follower_id == models.User.id,
    ).filter(
        models.user_follows.c.following_id == id,
    ).order_by(models.user_follows.c.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/{id}/following", response_model=list[schemas.UserOut])
def get_following(
    id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _get_target_user(db, id)
    return db.query(models.User).join(
        models.user_follows,
        models.user_follows.c.following_id == models.User.id,
    ).filter(
        models.user_follows.c.follower_id == id,
    ).order_by(models.user_follows.c.created_at.desc()).offset(skip).limit(limit).all()
