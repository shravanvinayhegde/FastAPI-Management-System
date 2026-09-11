from fastapi import status, HTTPException, Depends, APIRouter
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from app import models, schemas
from app.database import get_db
from sqlalchemy.orm import Session
from routers import oauth2

router = APIRouter(prefix="/vote", tags=["Vote"])

def _vote_status(db: Session, post_id: int, user_id: int) -> schemas.VoteStatus:
    post = db.query(models.Post.id).filter(models.Post.id == post_id).first()
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post does not exist")
    voted = db.query(models.Vote).filter(
        models.Vote.post_id == post_id,
        models.Vote.user_id == user_id,
    ).first() is not None
    vote_count = db.query(func.count(models.Vote.user_id)).filter(
        models.Vote.post_id == post_id,
    ).scalar() or 0
    return schemas.VoteStatus(voted=voted, vote_count=vote_count)


@router.post("/", response_model=schemas.VoteStatus, status_code=status.HTTP_200_OK)
def vote(
    vote: schemas.Vote,
    db: Session = Depends(get_db),
    current_user: int = Depends(oauth2.get_current_user)
):
    vote_query = db.query(models.Vote).filter(
        models.Vote.post_id == vote.post_id,
        models.Vote.user_id == current_user.id
    )
    found_vote = vote_query.first()

    if vote.dir == 1:
        if not found_vote:
            new_vote = models.Vote(post_id=vote.post_id, user_id=current_user.id)
            db.add(new_vote)
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
        return _vote_status(db, vote.post_id, current_user.id)
    if found_vote:
        vote_query.delete(synchronize_session=False)
        db.commit()
    return _vote_status(db, vote.post_id, current_user.id)


@router.get("/{post_id}/status", response_model=schemas.VoteStatus)
def get_vote_status(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(oauth2.get_current_user),
):
    return _vote_status(db, post_id, current_user.id)



