import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from routers import oauth2

router = APIRouter(prefix="/communities", tags=["Communities"])


def _optional_current_user(
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not token:
        return None
    credentials_exception = HTTPException(status_code=401, detail="Not valid credentials")
    token_data = oauth2.verify_access_token(token, credentials_exception)
    return db.query(models.User).filter(models.User.id == token_data.id).first()


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise HTTPException(status_code=422, detail="Community name must contain letters or numbers")
    return slug


def _get_community(db: Session, community_id: int) -> models.Community:
    community = db.query(models.Community).filter(models.Community.id == community_id).first()
    if community is None:
        raise HTTPException(status_code=404, detail="Community not found")
    return community


def _is_member(db: Session, community_id: int, user_id: int) -> bool:
    return db.query(models.community_members).filter(
        models.community_members.c.community_id == community_id,
        models.community_members.c.user_id == user_id,
    ).first() is not None


def _community_response(
    db: Session,
    community: models.Community,
    current_user: Optional[models.User] = None,
) -> schemas.CommunityOut:
    member_count = db.query(func.count()).select_from(models.community_members).filter(
        models.community_members.c.community_id == community.id,
    ).scalar() or 0
    return schemas.CommunityOut(
        id=community.id,
        name=community.name,
        slug=community.slug,
        description=community.description,
        creator_id=community.creator_id,
        created_at=community.created_at,
        updated_at=community.updated_at,
        member_count=member_count,
        is_member=current_user is not None and _is_member(db, community.id, current_user.id),
    )


@router.post("/", response_model=schemas.CommunityOut, status_code=status.HTTP_201_CREATED)
def create_community(
    community: schemas.CommunityCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    name = community.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Community name cannot be blank")
    slug = _slugify(name)
    duplicate = db.query(models.Community).filter(
        func.lower(models.Community.name) == name.lower()
    ).first()
    if duplicate or db.query(models.Community).filter(models.Community.slug == slug).first():
        raise HTTPException(status_code=409, detail="Community name or slug already exists")

    new_community = models.Community(
        name=name,
        slug=slug,
        description=community.description.strip(),
        creator_id=current_user.id,
    )
    db.add(new_community)
    try:
        db.flush()
        db.execute(models.community_members.insert().values(
            user_id=current_user.id,
            community_id=new_community.id,
        ))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Community name or slug already exists")
    db.refresh(new_community)
    return _community_response(db, new_community, current_user)


@router.get("/", response_model=list[schemas.CommunityOut])
def list_communities(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(_optional_current_user),
):
    query = db.query(models.Community)
    if search:
        query = query.filter(models.Community.name.ilike(f"%{search}%"))
    communities = query.order_by(models.Community.created_at.desc(), models.Community.id.desc()).offset(skip).limit(limit).all()
    return [_community_response(db, community, current_user) for community in communities]


@router.get("/{community_id}", response_model=schemas.CommunityOut)
def get_community(
    community_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[models.User] = Depends(_optional_current_user),
):
    return _community_response(db, _get_community(db, community_id), current_user)


@router.post("/{community_id}/join", response_model=schemas.CommunityOut)
def join_community(
    community_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    community = _get_community(db, community_id)
    if _is_member(db, community_id, current_user.id):
        raise HTTPException(status_code=409, detail="Already a community member")
    try:
        db.execute(models.community_members.insert().values(user_id=current_user.id, community_id=community_id))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Already a community member")
    return _community_response(db, community, current_user)


@router.delete("/{community_id}/join", status_code=status.HTTP_204_NO_CONTENT)
def leave_community(
    community_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    _get_community(db, community_id)
    result = db.execute(models.community_members.delete().where(
        models.community_members.c.community_id == community_id,
        models.community_members.c.user_id == current_user.id,
    ))
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Membership does not exist")
    db.commit()


@router.get("/{community_id}/members", response_model=list[schemas.UserOut])
def list_members(
    community_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    _get_community(db, community_id)
    return db.query(models.User).join(
        models.community_members,
        models.community_members.c.user_id == models.User.id,
    ).filter(
        models.community_members.c.community_id == community_id,
    ).order_by(models.community_members.c.joined_at.asc(), models.User.id.asc()).offset(skip).limit(limit).all()


@router.get("/{community_id}/posts", response_model=list[schemas.PostOut])
def list_community_posts(
    community_id: int,
    sort: str = Query("new", pattern="^(new|top|hot)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    _get_community(db, community_id)
    vote_count = func.count(models.Vote.post_id)
    query = db.query(models.Post, vote_count.label("votes")).outerjoin(
        models.Vote, models.Vote.post_id == models.Post.id,
    ).filter(
        models.Post.community_id == community_id,
        models.Post.published.is_(True),
    ).group_by(models.Post.id)
    if sort == "top":
        query = query.order_by(vote_count.desc(), models.Post.created_at.desc(), models.Post.id.desc())
    elif sort == "hot":
        age_hours = func.extract("epoch", func.now() - models.Post.created_at) / 3600.0
        hot_score = (vote_count + 1) / func.pow(age_hours + 2.0, 1.5)
        query = query.order_by(hot_score.desc(), models.Post.created_at.desc(), models.Post.id.desc())
    else:
        query = query.order_by(models.Post.created_at.desc(), models.Post.id.desc())
    results = query.offset(skip).limit(limit).all()
    return [{"Post": post, "votes": votes} for post, votes in results]
