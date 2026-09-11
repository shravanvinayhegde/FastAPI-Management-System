import json

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, conint
from datetime import datetime
from typing import Optional

class PostBase(BaseModel):
    title: str
    content: str
    published: bool = True
    community_id: Optional[int] = None
    image_url: Optional[AnyHttpUrl] = None
    video_url: Optional[AnyHttpUrl] = None

class PostCreate(PostBase):
    pass


class UserOut(BaseModel):  
    id: int
    username: Optional[str] = None
    email: str
    created_at: datetime
    display_name: Optional[str] = None
    bio: Optional[str] = None
    avatar_url: Optional[str] = None
    profile_visibility: Optional[str] = None
    show_posts: Optional[bool] = None
    show_communities: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)


class FollowStatus(BaseModel):
    following: bool
    follower_count: int
    following_count: int


class ProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)
    bio: Optional[str] = Field(default=None, max_length=1000)
    profile_visibility: Optional[str] = Field(default=None, pattern="^(public|private)$")
    show_posts: Optional[bool] = None
    show_communities: Optional[bool] = None


class ProfileUser(BaseModel):
    id: int
    username: str
    display_name: str
    bio: Optional[str]
    avatar_url: str
    created_at: datetime


class ProfileStats(BaseModel):
    followers: int
    following: int
    posts: int
    communities: int


class ProfileRelationship(BaseModel):
    is_following: bool
    is_followed_by: bool


class ProfileActions(BaseModel):
    can_follow: bool
    can_message: bool
    is_self: bool


class ProfilePrivacy(BaseModel):
    visibility: str
    show_posts: bool
    show_communities: bool


class ProfileResponse(BaseModel):
    user: ProfileUser
    stats: ProfileStats
    relationship: ProfileRelationship
    actions: ProfileActions
    privacy: ProfilePrivacy


class PublicUser(BaseModel):
    id: int
    username: str
    display_name: str
    avatar_url: str

    model_config = ConfigDict(from_attributes=True)


class CommunityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)


class CommunityOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    creator_id: int
    created_at: datetime
    updated_at: datetime
    member_count: int = 0
    is_member: bool = False
    model_config = ConfigDict(from_attributes=True)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    shared_post_id: Optional[int] = None


class SharedPostPreview(BaseModel):
    id: int
    title: str
    content: str
    owner_id: int

    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    id: int
    conversation_id: int
    sender_id: int
    content: str
    shared_post_id: Optional[int] = None
    shared_post: Optional[SharedPostPreview] = None
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ConversationCreate(BaseModel):
    user_id: int


class ConversationOut(BaseModel):
    id: int
    user_one_id: int
    user_two_id: int
    created_at: datetime
    updated_at: datetime
    other_user: PublicUser
    last_message: Optional[MessageOut] = None
    unread_count: int = 0
    model_config = ConfigDict(from_attributes=True)


class NotificationOut(BaseModel):
    id: int
    recipient_id: int
    actor_id: Optional[int]
    type: str
    entity_type: str
    entity_id: Optional[int]
    payload: dict[str, object]
    is_read: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

    @field_validator("payload", mode="before")
    @classmethod
    def parse_payload(cls, value: object) -> dict[str, object]:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value if isinstance(value, dict) else {}


class ReplyCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    parent_id: Optional[int] = None


class ReplyOut(BaseModel):
    id: int
    post_id: int
    parent_id: Optional[int]
    owner_id: int
    content: str
    created_at: datetime
    updated_at: datetime
    owner: Optional[UserOut] = None
    model_config = ConfigDict(from_attributes=True)


class PostMediaOut(BaseModel):
    id: int
    url: str
    media_type: str
    mime_type: str
    size_bytes: int
    width: Optional[int] = None
    height: Optional[int] = None
    duration_seconds: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class MediaUploadOut(BaseModel):
    url: str
    media_type: str
    mime_type: str
    size: int


class ShareOut(BaseModel):
    post_id: int
    shared: bool
    share_count: int
    url: Optional[str] = None
    

class Post(PostBase):
    id: int          
    created_at: datetime
    owner_id:int
    owner:UserOut
    media: list[PostMediaOut] = []

    model_config = ConfigDict(from_attributes=True)

class PostOut(BaseModel):
    Post: Post
    votes: int

    model_config = ConfigDict(from_attributes=True)

class UserCreate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=3, max_length=50, pattern="^[a-zA-Z0-9_]+$")
    email: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):    
    access_token: str
    token_type: str

class TokenData(BaseModel):
    id: Optional[str] = None


class Vote(BaseModel):  
    post_id: int
    dir: conint(ge=0, le=1)   # type: ignore


class VoteStatus(BaseModel):
    voted: bool
    vote_count: int


