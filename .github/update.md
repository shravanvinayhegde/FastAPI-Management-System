VoteFlow Backend — Exact Fix Instructions

Repository: shravanvinayhegde/FastAPI-Management-System

Latest commit inspected: 71486ee96c02e5a46e419321b2d5a5be0fcd3e89 (Implement backend reliability and media contracts).

Scope

These instructions target the current backend contract and the reported failures:

profile lookup returning user does not exist;

multipart image/video post upload failing;

community slug links;

profile-to-DM behavior;

standard post sharing into DMs;

durable uploaded media;

profile media responses;

production deployment/migration consistency.

1. Fix the post creation endpoint — this is the main upload bug

File

routers/post.py

Current mismatch

The frontend latest push sends:

POST /posts/
Content-Type: multipart/form-data

title
content
published
community_id
image
video

But the backend latest POST /posts/ still declares:

def create_posts(
    post: schemas.PostCreate,
    ...
):

PostCreate is a JSON/Pydantic body model.

It does not consume the frontend's multipart image and video file fields.

This is the primary frontend/backend contract mismatch behind image/video posting failure.

Required solution

Change POST /posts/ to a multipart endpoint.

Use:

from fastapi import File, Form, UploadFile

and define approximately:

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
    ...

Do not attempt to read the multipart request into schemas.PostCreate.

2. Refactor media validation into a shared helper

Current problem

upload_post_media() is itself an API endpoint:

@router.post("/media")
async def upload_post_media(...)

but attach_post_media() calls that endpoint function internally.

This mixes route handling and storage/business logic.

Required architecture

Create a private helper:

async def _store_post_media(file: UploadFile) -> schemas.MediaUploadOut:
    ...

It should:

validate MIME/content type;

read within the configured size limit;

validate actual image format/container;

generate UUID filename;

write to configured storage;

return the stored media metadata.

Then use _store_post_media() from both:

POST /posts/
POST /posts/{post_id}/media

3. Protect /posts/media

Security issue

The current standalone endpoint:

POST /posts/media

has no current_user dependency.

That means anyone who can reach the API may be able to upload files to the media directory without authentication.

Fix

Add:

current_user: models.User = Depends(oauth2.get_current_user)

or remove the standalone endpoint if the application will only upload through POST /posts/.

Preferred product design: keep one authenticated multipart post-creation endpoint and remove redundant unauthenticated upload behavior.

4. During multipart post creation, store media in post_media

When the post is created:

1. Validate community membership.
2. Validate title/content.
3. Insert Post.
4. Flush to get post.id.
5. Store image/video files.
6. Create PostMedia rows with post_id.
7. Commit transaction.
8. Return Post with media.

Do not put uploaded files directly into posts.image_url / posts.video_url as the new design.

Use PostMedia.

5. Make multipart creation transactional

The upload workflow must not create a database post and then fail halfway through without cleanup.

Recommended flow:

validate everything first
        ↓
store temporary files
        ↓
DB insert Post
        ↓
DB insert PostMedia
        ↓
commit

On database failure:

rollback DB
remove newly stored files

On media validation failure:

no Post row should remain

This prevents orphan posts/files.

6. Return media metadata on post responses

The backend model/schema already has:

media: list[PostMediaOut]

Keep this.

All post response paths must populate it:

GET /posts/
GET /posts/{post_id}
GET /users/{username}/posts
GET /users/{username}/likes
GET /communities/{community_id}/posts
POST /posts/
PUT /posts/{post_id}

Avoid a situation where create responses contain media but feed responses don't.

7. Make legacy URL fields backward compatible

The backend still has:

image_url
video_url

Keep them temporarily for old data if they are already stored.

New uploads should use post_media.

Do not force an immediate destructive migration.

Recommended compatibility logic:

new post
→ post_media

old post
→ legacy image_url/video_url

Optionally migrate old URLs later.

8. Fix GET /communities/by-slug/{slug} contract

The latest backend provides:

GET /communities/by-slug/{slug}

Keep this endpoint.

The frontend must call exactly this path.

Do not rename it to /communities/slug/{slug} unless you change both repositories together.

The cleanest fix is to keep the backend path and correct the frontend.

9. Ensure community slug lookup is case-consistent

Current lookup is effectively:

func.lower(models.Community.slug) == slug.lower()

Keep that behavior.

Also make sure every new community gets:

non-empty slug
unique slug
stable slug

10. Fix profile lookup for every user

File

routers/profile.py

Current lookup is:

func.lower(models.User.username) == username.lower()

This is correct.

If production returns:

User profile not found

then the database has no matching username.

Do not replace the profile endpoint with email/id lookup just to hide bad data.

11. Verify the profile migration is actually applied in production

Migration:

f6a7b8c9d0e1_add_user_profiles.py

The migration:

adds username;

adds display_name;

fills existing users from email local-parts;

makes username non-null;

adds unique username constraint;

adds profile visibility/settings.

This is correct in principle.

Production requirement

Run:

alembic current
alembic heads
alembic upgrade head

Then verify:

SELECT id, username, email, display_name
FROM users
ORDER BY id;

There must be a valid non-null username for every existing user.

12. Add a production data check for usernames

Run:

SELECT COUNT(*)
FROM users
WHERE username IS NULL OR username = '';

Expected:

0

Check duplicates:

SELECT LOWER(username), COUNT(*)
FROM users
GROUP BY LOWER(username)
HAVING COUNT(*) > 1;

Expected:

0 rows

Check a specific failing profile:

SELECT id, username, email, display_name
FROM users
WHERE LOWER(username) = LOWER('karan_2');

If this returns zero rows, the frontend is not the root cause.

13. Keep username generation consistent for registration

routers/user.py currently generates the username when registration does not supply one.

Keep this behavior.

However, ensure:

email uniqueness
username uniqueness

are database enforced and the IntegrityError handler is retained.

The JWT must continue storing:

{"user_id": user.id}

because the frontend already correctly reads user_id from the token.

14. Keep profile response actions

The backend's latest ProfileResponse already contains:

actions=schemas.ProfileActions(
    can_follow=...,
    can_message=...,
    is_self=...,
)

Keep this.

It gives the frontend a reliable contract for profile action buttons.

15. Improve can_message semantics

Current behavior should allow an authenticated user to message another user regardless of whether they follow them.

Recommended:

can_message = viewer is not None and viewer.id != target.id

Do not make messaging conditional on following unless that is an explicit product requirement.

16. Fix conversation participant contract

The latest backend already enriches ConversationOut with:

other_user
last_message
unread_count

Keep that.

The frontend should not need to show:

Conversation with user 7

anymore.

other_user should always be present for a valid authenticated 1-to-1 conversation.

17. Avoid N+1 conversation queries as the app grows

The current _conversation_response() performs separate queries for:

other user
last message
conversation member
unread count

per conversation.

That is acceptable for a small application but will scale poorly.

Later, replace it with joined/subqueries or batched queries.

This is not required to fix the immediate profile/upload issue.

18. Shared post messages are already supported — finish the contract

The backend now has:

MessageCreate.shared_post_id
MessageOut.shared_post_id
MessageOut.shared_post

Keep this.

When shared_post_id is supplied:

verify the post exists;

verify it is published;

store shared_post_id;

return shared_post in the message response.

The current backend performs the important existence/published check.

19. Improve the shared post preview

Current preview contains:

id
title
content
owner_id

For a standard social share card, add:

owner
media
community
created_at

or a compact nested structure:

{
  "id": 123,
  "title": "Example",
  "content": "Preview...",
  "owner": {
    "id": 7,
    "username": "karan_2",
    "display_name": "Karan"
  },
  "media": [...]
}

The frontend can then render a proper shared-post card in chat.

20. Canonical post URL

The Share endpoint already returns a URL based on:

settings.frontend_url

with fallback to:

/post/{post_id}

Set in production:

frontend_url=https://voteflow-phi.vercel.app

Then the API returns:

https://voteflow-phi.vercel.app/post/123

This is preferable to returning only a relative URL.

21. Fix production media persistence

The latest backend uses:

settings.media_directory

and stores files under:

media/posts
media/avatars

This works only while the underlying storage is persistent.

For production use either:

persistent volume

or:

object storage

Do not assume a normal ephemeral container filesystem is durable across redeploys.

22. Fix media path security

The backend already uses UUID filenames.

Keep that.

Do not use:

file.filename

directly as the stored path.

Continue validating:

actual image format
actual video container
file size
image dimensions

23. Populate media dimensions correctly

The image upload helper already reads:

width, height = image.size

but the current MediaUploadOut does not expose those values.

Add them to the upload result if the frontend will use them:

width
height

For videos, duration is optional unless extracted with a media parser.

24. Do not require ffmpeg just to ship the first working version

For initial functionality:

accept MP4/WebM/MOV
validate container signatures
store file
serve file

Duration/codec metadata can be added later.

Avoid making ffmpeg a hard deployment dependency unless needed.

25. Verify CORS for Vercel + multipart requests

Production config must allow:

https://voteflow-phi.vercel.app

and the methods/headers used by:

POST /posts/
POST /users/me/avatar
POST /conversations
POST /posts/{id}/share

Multipart requests with an Authorization header must successfully complete CORS negotiation.

Do not use a wildcard origin together with credentials.

26. Diagnose Failed to fetch correctly

A browser-level Failed to fetch can mean:

wrong backend URL
CORS failure
backend unavailable
TLS/network failure
frontend deployed with old environment variable

It does NOT by itself mean the FastAPI handler returned a 4xx.

After the frontend is updated, inspect DevTools → Network.

For a successful image post, the request should look like:

POST https://fastapi-management-system.onrender.com/posts/
Status: 201
Content-Type: multipart/form-data; boundary=...

27. Test the profile endpoint directly after deployment

Using a valid username:

GET https://fastapi-management-system.onrender.com/users/<username>/profile

For a public profile, expected:

200

For an inaccessible private profile:

403

For a nonexistent username:

404 User profile not found

For the reported bug, this direct API test is the decisive diagnostic.

28. Test multipart posting with curl after deployment

Example:

curl -X POST \
  "https://fastapi-management-system.onrender.com/posts/" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "title=Test image post" \
  -F "content=Testing direct upload" \
  -F "published=true" \
  -F "image=@test.jpg"

Expected:

201 Created

with a post response containing:

"media": [
  {
    "url": "/media/posts/...",
    "media_type": "image"
  }
]

Then verify the media URL is actually served.

29. Ensure /posts/{post_id} remains available for shared links

The backend latest push already provides canonical post detail lookup.

Keep:

GET /posts/{post_id}

and ensure it returns:

Post
votes
media
owner

for published posts.

30. Backend acceptance tests

Profiles

Existing users all have usernames.

Username is unique in DB.

/users/{username}/profile returns 200 for public profiles.

Own private profile returns 200.

Follower-accessible private profile returns 200.

Unauthorized private profile returns 403.

Unknown username returns 404.

Posts/media

JSON-only legacy post path is handled intentionally.

Multipart post creation works.

Image upload works.

Video upload works.

Invalid image rejected.

Invalid video rejected.

Oversized image rejected.

Oversized video rejected.

Media is saved in post_media.

Created post response contains media.

Feed response contains media.

Media survives production restart when persistent storage is configured.

Communities

Community has a slug.

Slug is unique.

/communities/by-slug/{slug} returns the community.

Membership state is correct.

Community post requires membership.

Messaging

Creating a conversation returns other_user.

Existing conversation is reused.

Messages list returns latest message/unread count where applicable.

Shared post message works.

Shared post preview is returned.

Sharing

Share is persisted once per user.

Share count is correct.

Canonical frontend URL is returned when frontend_url is configured.