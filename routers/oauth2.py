from jose import JWTError, jwt
from app import schemas, database, models
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from fastapi import Depends, status, HTTPException
from fastapi.security import OAuth2PasswordBearer
from app.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl='login')
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl='login', auto_error=False)

SECRET_KEY = settings.secret_key                            
ALGORITHM = settings.algorithm                              
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes  

def create_access_token(data: dict):
    to_encode = data.copy()
    expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expires})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_access_token(token: str, credentials_exception):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise credentials_exception
        token_data = schemas.TokenData(id=int(user_id))
    except (JWTError, TypeError, ValueError):
        raise credentials_exception
    return token_data

def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(database.get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not valid credentials",
        headers={"WWW-Authenticate": "Bearer"}
    )
    token_data = verify_access_token(token, credentials_exception)
    user = db.query(models.User).filter(models.User.id == token_data.id).first()
    if user is None:
        raise credentials_exception
    return user