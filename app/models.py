from sqlalchemy import CheckConstraint, Column, Integer, String, Boolean, TIMESTAMP, Table, text, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from .database import Base

user_follows = Table(
    "user_follows",
    Base.metadata,
    Column("follower_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True),
    Column("following_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")),
    CheckConstraint("follower_id <> following_id", name="ck_user_follows_no_self_follow"),
)


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, nullable=False)
    title = Column(String, nullable=False)
    content = Column(String, nullable=False)
    image_url = Column(String(2048), nullable=True)
    video_url = Column(String(2048), nullable=True)
    published = Column(Boolean, server_default='True', default=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('NOW()'))
    owner_id=Column(Integer,ForeignKey("users.id",ondelete="CASCADE"),nullable=False)  
    community_id = Column(Integer, ForeignKey("communities.id", ondelete="SET NULL"), nullable=True, index=True)
    owner=relationship("User")
    community = relationship("Community", back_populates="posts")
    replies = relationship("PostReply", back_populates="post", cascade="all, delete-orphan")
    shares = relationship("PostShare", back_populates="post", cascade="all, delete-orphan")
    media = relationship("PostMedia", back_populates="post", cascade="all, delete-orphan")


class PostMedia(Base):
    __tablename__ = "post_media"

    id = Column(Integer, primary_key=True, nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    media_type = Column(String(20), nullable=False)
    storage_key = Column(String(2048), nullable=False)
    mime_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    post = relationship("Post", back_populates="media")

    @property
    def url(self) -> str:
        return f"/media/{self.storage_key}"


class User(Base):  
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, nullable=False)
    username = Column(String(50), nullable=False, unique=True, index=True)
    email = Column(String, nullable=False, unique=True)
    password = Column(String, nullable=False)
    display_name = Column(String(100), nullable=False)
    bio = Column(String(1000), nullable=True)
    avatar_url = Column(String(2048), nullable=True)
    avatar_type = Column(String(20), nullable=False, default="default", server_default="default")
    profile_visibility = Column(String(20), nullable=False, default="public", server_default="public")
    show_posts = Column(Boolean, nullable=False, default=True, server_default="true")
    show_communities = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('NOW()'))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text('NOW()'))
    followers = relationship(
        "User",
        secondary=user_follows,
        primaryjoin=id == user_follows.c.following_id,
        secondaryjoin=id == user_follows.c.follower_id,
        back_populates="following",
    )
    following = relationship(
        "User",
        secondary=user_follows,
        primaryjoin=id == user_follows.c.follower_id,
        secondaryjoin=id == user_follows.c.following_id,
        back_populates="followers",
    )
    created_communities = relationship("Community", back_populates="creator")
    communities = relationship("Community", secondary="community_members", back_populates="members")
    replies = relationship("PostReply", back_populates="owner")


class Vote(Base):                    
    __tablename__ = "votes"          

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)


class Community(Base):
    __tablename__ = "communities"

    id = Column(Integer, primary_key=True, nullable=False)
    name = Column(String(100), nullable=False, unique=True, index=True)
    slug = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(String(500), nullable=False, default="", server_default="")
    creator_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    creator = relationship("User", back_populates="created_communities")
    members = relationship("User", secondary="community_members", back_populates="communities")
    posts = relationship("Post", back_populates="community")


community_members = Table(
    "community_members",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True),
    Column("community_id", ForeignKey("communities.id", ondelete="CASCADE"), primary_key=True, index=True),
    Column("joined_at", TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()")),
)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("user_one_id", "user_two_id", name="uq_conversations_user_pair"),
        CheckConstraint("user_one_id < user_two_id", name="ck_conversations_ordered_users"),
    )

    id = Column(Integer, primary_key=True, nullable=False)
    user_one_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    user_two_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    members = relationship("ConversationMember", back_populates="conversation", cascade="all, delete-orphan")


class ConversationMember(Base):
    __tablename__ = "conversation_members"
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    joined_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))
    last_read_at = Column(TIMESTAMP(timezone=True), nullable=True)

    conversation = relationship("Conversation", back_populates="members")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, nullable=False)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(String(2000), nullable=False)
    shared_post_id = Column(Integer, ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"), index=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    conversation = relationship("Conversation", back_populates="messages")
    shared_post = relationship("Post")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, nullable=False)
    recipient_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    type = Column(String(50), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(Integer, nullable=True)
    payload = Column(String(2000), nullable=False, default="{}", server_default="{}")
    is_read = Column(Boolean, nullable=False, default=False, server_default="false", index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"), index=True)


class PostReply(Base):
    __tablename__ = "post_replies"

    id = Column(Integer, primary_key=True, nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("post_replies.id", ondelete="CASCADE"), nullable=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(String(2000), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"), index=True)
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    post = relationship("Post", back_populates="replies")
    owner = relationship("User", back_populates="replies")
    parent = relationship("PostReply", remote_side=[id], back_populates="children")
    children = relationship("PostReply", back_populates="parent", cascade="all, delete-orphan")


class PostShare(Base):
    __tablename__ = "post_shares"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_shares_post_user"),)

    id = Column(Integer, primary_key=True, nullable=False)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"), index=True)

    post = relationship("Post", back_populates="shares")
