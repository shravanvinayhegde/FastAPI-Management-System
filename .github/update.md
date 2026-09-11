# Backend Fixes — FastAPI Social Management System

Repository:

`shravanvinayhegde/FastAPI-Management-System`

This document covers backend issues that affect the reliability, correctness, scalability, and feature behavior of the social-management web application.

---

# 1. Make media storage production-safe

**Priority: 🔴 CRITICAL**

## File

```text
routers/profile.py
```

## Problem

Avatar files are stored locally:

```python
AVATAR_DIRECTORY = Path("media") / "avatars"
```

and:

```python
path.write_bytes(data)
```

This works on a local machine.

However, production servers may use ephemeral/containerized filesystems.

That creates a potentially dangerous flow:

```text
Upload avatar
    ↓
File stored on server filesystem
    ↓
Application restart/redeploy
    ↓
File may disappear
```

## Fix

Use persistent storage.

Recommended choices:

```text
Object storage
├── S3
├── Cloudflare R2
├── Supabase Storage
└── Cloudinary
```

or a hosting provider's persistent volume.

The database should store the durable URL/object key rather than depending on local ephemeral files.

---

# 2. Keep media URL contract consistent

**Priority: 🔴 CRITICAL**

The backend currently stores:

```text
/media/avatars/file.jpg
```

This is valid if the client knows that `/media` belongs to the API server.

Document and enforce the contract consistently.

Recommended database value:

```text
/media/avatars/<filename>
```

Then the frontend resolves that against:

```text
NEXT_PUBLIC_API_URL
```

Do not randomly mix:

```text
/media/...
https://api...
```

unless there is a clear reason.

---

# 3. Add vote status endpoint

**Priority: 🔴 HIGH**

## File

```text
routers/vote.py
```

Current endpoint:

```text
POST /vote/
```

supports:

```text
dir = 1 → add
dir = 0 → remove
```

but it doesn't tell the frontend whether the user already voted.

## Fix

Add:

```http
GET /vote/{post_id}/status
```

Response:

```json
{
  "voted": true
}
```

A better solution is to include vote status directly in the post response.

For example:

```json
{
  "Post": {...},
  "votes": 12,
  "user_voted": true
}
```

This gives the frontend everything it needs.

---

# 4. Make voting idempotent where appropriate

**Priority: 🟠 HIGH**

Current behavior:

```text
vote already exists
↓
POST dir=1
↓
409 Conflict
```

This is technically valid, but it makes UI integration unnecessarily fragile.

You can instead make the operation behave like:

```text
dir=1 + already voted
→ keep voted state
```

and:

```text
dir=0 + not voted
→ keep not-voted state
```

Another option is to retain the strict API and have the frontend check status first.

For a user-facing social application, an idempotent toggle-style API is generally easier to use.

---

# 5. Fix `top` and `hot` community sorting

**Priority: 🟠 HIGH**

## File

```text
routers/community.py
```

Current implementation effectively does:

```python
if sort == "top":
    order_by(vote_count.desc(), ...)
elif sort == "hot":
    order_by(vote_count.desc(), ...)
```

Therefore:

```text
TOP
=
HOT
```

## Fix

Define separate algorithms.

### Top

Could be:

```text
highest vote count
```

### New

Could be:

```text
newest posts
```

### Hot

Should consider both:

```text
votes
age
engagement
```

A simple hotness approximation could use time decay.

For example:

```text
score = votes / ((age_hours + 2) ^ 1.5)
```

The exact formula is up to the product design, but `hot` should not simply duplicate `top`.

---

# 6. Ensure database constraints exist

**Priority: 🔴 HIGH**

Application-level duplicate checking exists in places such as:

```text
community
username
follow relationships
```

but important uniqueness rules should also exist in the database.

Recommended constraints:

```text
users.email
users.username
communities.slug
(follower_id, following_id)
(post_id, user_id) for votes
(post_id, user_id) for shares
(conversation/user pair)
(community_id, user_id)
```

## Why

Application code can race.

Example:

```text
Request A → checks username → available
Request B → checks username → available

Request A → inserts
Request B → inserts
```

A DB-level unique constraint guarantees correctness.

---

# 7. Make follow operations race-safe

**Priority: 🟠 HIGH**

The backend already catches:

```python
IntegrityError
```

which is good.

Keep a unique constraint on:

```text
follower_id + following_id
```

so simultaneous requests cannot create duplicate relationships.

---

# 8. Make conversation uniqueness database-enforced

**Priority: 🟠 HIGH**

The backend sorts the two IDs:

```python
user_one_id, user_two_id = sorted((...))
```

which is good.

But the database should also guarantee that:

```text
(user_one_id, user_two_id)
```

is unique.

Otherwise concurrent requests can theoretically create duplicate conversations.

---

# 9. Fix WebSocket scalability

**Priority: 🟠 HIGH**

## File

```text
routers/chat.py
```

Current architecture:

```python
self.connections: dict[int, set[WebSocket]]
```

This connection registry exists only inside one Python process.

Therefore:

```text
Worker A:
User 1 websocket

Worker B:
User 2 sends message
```

Worker B cannot see Worker A's socket.

## Fix for scaling

Use:

```text
              Redis Pub/Sub
                  |
        ┌─────────┴─────────┐
        ↓                   ↓
 FastAPI worker A      FastAPI worker B
        ↓                   ↓
 WebSocket users       WebSocket users
```

When a message occurs:

```text
HTTP request
   ↓
Database commit
   ↓
Redis publish
   ↓
all relevant workers
   ↓
WebSocket delivery
```

## For the current deployment

If the application runs one worker/process, the current implementation can work.

Do not introduce unnecessary distributed infrastructure before you need it.

---

# 10. Handle WebSocket authentication failure distinctly

**Priority: 🟠 MEDIUM**

Current WebSocket authentication is:

```text
/ws/events?token=...
```

The server validates the JWT.

That's acceptable for this architecture, but invalid tokens should not enter an endless reconnect loop.

Current frontend reconnection can repeatedly attempt to connect.

Recommended behavior:

```text
valid token
→ reconnect on network failure

invalid/expired token
→ close permanently
→ require login
```

The WebSocket protocol should communicate an authentication failure clearly.

---

# 11. Improve notification generation

**Priority: 🟠 HIGH**

Current notification handling covers important actions such as:

```text
new follower
new message
```

The social system should consistently generate notifications for relevant interactions.

At minimum:

```text
follow
message
reply
reply to reply
```

Potentially:

```text
post interaction
community activity
```

depending on the intended product behavior.

---

# 12. Standardize notification types

**Priority: 🟡 MEDIUM**

Use a fixed set of backend notification names.

For example:

```text
NEW_FOLLOWER
NEW_MESSAGE
NEW_REPLY
NEW_REPLY_TO_REPLY
```

Avoid arbitrary strings being created throughout the code.

Better:

```python
class NotificationType(str, Enum):
    NEW_FOLLOWER = "NEW_FOLLOWER"
    NEW_MESSAGE = "NEW_MESSAGE"
    NEW_REPLY = "NEW_REPLY"
    NEW_REPLY_TO_REPLY = "NEW_REPLY_TO_REPLY"
```

This helps frontend rendering and validation.

---

# 13. Standardize notification payloads

**Priority: 🟠 HIGH**

Backend currently returns:

```python
payload: dict[str, object]
```

Keep that design.

Define the expected structure for each notification.

Example:

### Follow

```json
{
  "follower_id": 12,
  "username": "shravan"
}
```

### Message

```json
{
  "conversation_id": 33,
  "message_id": 91
}
```

### Reply

```json
{
  "post_id": 50,
  "reply_id": 88
}
```

Do not create arbitrary payload structures for the same notification type.

---

# 14. Consolidate profile APIs

**Priority: 🟠 HIGH**

There are effectively two profile styles:

```text
/users/{id}
```

and:

```text
/users/{username}/profile
```

The newer profile router handles:

```text
visibility
posts visibility
communities visibility
relationship
profile data
```

This makes it preferable as the canonical profile API.

## Recommended architecture

Canonical public profile:

```text
GET /users/{username}/profile
```

Profile posts:

```text
GET /users/{username}/posts
```

Followers:

```text
GET /users/{username}/followers
```

Following:

```text
GET /users/{username}/following
```

Communities:

```text
GET /users/{username}/communities
```

Keep `/users/{id}` only where ID lookup is genuinely needed.

---

# 15. Apply profile privacy consistently

**Priority: 🔴 HIGH**

Privacy logic exists in the newer profile implementation.

But every endpoint exposing profile-related information needs to obey the same rules.

For a private profile:

```text
anonymous
→ denied

non-follower
→ denied

follower
→ allowed

profile owner
→ allowed
```

The same rule should apply consistently to:

```text
profile
posts
followers
following
communities
```

---

# 16. Make post visibility rules explicit

**Priority: 🟠 HIGH**

The general post endpoint currently returns posts without applying a sophisticated visibility layer.

You should explicitly define:

```text
published = true
```

and decide what happens to:

```text
published = false
```

for:

```text
owner
followers
community members
anonymous users
```

Do not let drafts accidentally appear in public feeds.

---

# 17. Validate community membership before posting

**Priority: ✅ Already mostly correct**

The backend currently checks whether a user is a member before allowing a community post.

Keep this rule:

```text
create community post
        ↓
community exists?
        ↓
user is member?
        ↓
allow post
```

This is one of the better authorization paths in the current backend.

---

# 18. Prevent creator from accidentally losing special community state

**Priority: 🟠 MEDIUM**

When creating a community, the creator is automatically inserted as a member.

That is good.

However, decide explicitly what happens when the creator chooses:

```text
Leave community
```

Questions the backend should answer:

```text
Can creator leave?
Does ownership transfer?
Can community become ownerless?
```

The API currently allows leaving if membership exists.

A community system should have an explicit ownership policy.

---

# 19. Fix avatar file lifecycle

**Priority: 🟠 MEDIUM**

Current avatar deletion correctly attempts to delete uploaded files.

When moving to object storage, implement:

```text
delete DB reference
+
delete storage object
```

Also consider what happens when replacing an existing avatar:

```text
old avatar
      ↓
upload new avatar
      ↓
new DB URL
```

The old file should eventually be removed.

Otherwise uploads accumulate indefinitely.

---

# 20. Validate uploaded media more thoroughly

**Priority: 🟠 MEDIUM**

Avatar validation is already reasonably good:

```text
size
format
dimensions
PIL verification
```

Keep those checks.

For post media, use similarly strict validation:

```text
maximum file size
allowed MIME type
allowed extension
actual file signature
```

Never trust the browser-provided MIME type alone.

---

# 21. Add transaction boundaries around multi-step operations

**Priority: 🟠 HIGH**

Operations such as:

```text
create community
create membership
create notification
send message
create follow
```

often modify multiple database records.

Make sure related database writes happen atomically where appropriate.

Example:

```text
create follow
+
create notification
```

should not leave the database in an inconsistent state.

---

# 22. Do not rely on background tasks for critical persistence

The follow endpoint creates the notification in the database before scheduling the WebSocket notification.

That is the correct order:

```text
database
    ↓
commit
    ↓
realtime notification
```

Keep that approach.

The WebSocket event is a delivery mechanism, not the source of truth.

---

# 23. Treat HTTP/database state as authoritative

The backend should remain the authority for:

```text
votes
followers
community membership
messages
notifications
posts
profile settings
```

WebSockets should only accelerate updates.

Correct architecture:

```text
HTTP API
    ↓
Database
    ↓
authoritative state

WebSocket
    ↓
real-time convenience
```

If a WebSocket event is missed, the application should be able to recover using HTTP.

Your messaging implementation already partially follows this model.

---

# 24. Improve API response consistency

Some endpoints return:

```json
{
  "message": "..."
}
```

while others return complete resources.

For interactive APIs, consider returning authoritative state after mutation.

Example:

### Follow

Better:

```json
{
  "following": true,
  "follower_count": 20,
  "following_count": 15
}
```

Current backend already does something similar, which is good.

Apply the same principle to:

```text
votes
community membership
notifications
profile updates
```

---

# 25. Add tests for the important social flows

**Priority: 🔴 HIGH**

Add backend tests for:

## Authentication

```text
register
login
invalid password
expired token
```

## Users

```text
follow
unfollow
duplicate follow
self follow
followers
following
```

## Posts

```text
create
edit
delete
unauthorized edit
unauthorized delete
```

## Votes

```text
vote
remove vote
duplicate vote
wrong-user removal
```

## Replies

```text
create
nested reply
invalid parent
edit
delete
wrong-user edit
wrong-user delete
```

## Communities

```text
create
duplicate community
join
duplicate join
leave
post to community
non-member posting
```

## Messaging

```text
create conversation
duplicate conversation
send message
unauthorized conversation
edit own message
edit somebody else's message
delete own message
delete somebody else's message
```

## Notifications

```text
list
unread count
mark read
mark all read
```

---

# 26. Add API contract tests

The biggest integration issue in the two repositories is contract drift.

Create tests that verify:

```text
backend response
       ↓
matches frontend expected structure
```

Especially for:

```text
Notification
Message
Post
Profile
Community
```

The `Notification.payload` mismatch is exactly the type of problem these tests should catch.

---

# 27. Clean repository artifacts

The backend repository contains committed:

```text
__pycache__/
*.pyc
```

These should not be tracked.

Remove them from git and ensure `.gitignore` contains:

```gitignore
__pycache__/
*.py[cod]
*$py.class
```

This doesn't directly break a feature, but it keeps the repository clean and prevents generated Python artifacts from being versioned.

---

# 28. Keep database migrations synchronized

The backend contains multiple Alembic migrations covering:

```text
users
posts
votes
follows
communities
messaging
media
profiles
```

Every model/schema change should have a corresponding migration.

Deployment flow should be:

```text
git push
   ↓
deploy
   ↓
alembic upgrade head
   ↓
start FastAPI
```

Never depend on manually modifying production databases.

---

# 29. Verify CORS configuration

## File

```text
app/main.py
app/config.py
```

The backend uses:

```python
CORSMiddleware
```

and reads origins from configuration.

Production should explicitly allow the deployed Vercel origin.

For example:

```text
https://voteflow-phi.vercel.app
```

and local development:

```text
http://localhost:3000
```

Do not use overly broad production CORS settings unnecessarily.

---

# 30. Verify Render/WebSocket deployment behavior

Normal HTTP:

```text
https://fastapi-management-system.onrender.com
```

WebSocket:

```text
wss://fastapi-management-system.onrender.com/ws/events
```

Make sure the production infrastructure actually supports WebSocket connections for the deployed service.

A working:

```text
GET /health
```

does not prove:

```text
WebSocket /ws/events
```

is working.

Test both independently.

---

# Backend Priority Order

```text
P0
├── 1. Production media persistence
├── 2. Consistent media URL contract
├── 3. Vote state endpoint
└── 4. Profile/privacy consistency

P1
├── 5. Database uniqueness constraints
├── 6. WebSocket reliability/scaling
├── 7. Notification contract
├── 8. Notification coverage
├── 9. Post visibility
├── 10. Community ownership rules
└── 11. Backend integration tests

P2
├── 12. Hot/top algorithm
├── 13. Media lifecycle cleanup
├── 14. API response consistency
├── 15. CORS/deployment hardening
└── 16. Repository/migration cleanup
```

# Backend Definition of Done

The backend should pass:

```text
[ ] register
[ ] login
[ ] invalid login
[ ] authenticated requests
[ ] expired token rejection
[ ] create post
[ ] update own post
[ ] reject editing another user's post
[ ] delete own post
[ ] reject deleting another user's post
[ ] vote
[ ] remove vote
[ ] duplicate vote handling
[ ] vote status
[ ] create reply
[ ] create nested reply
[ ] reject invalid parent
[ ] edit reply
[ ] delete reply
[ ] follow user
[ ] unfollow user
[ ] reject self-follow
[ ] duplicate-follow protection
[ ] create community
[ ] duplicate community protection
[ ] join community
[ ] duplicate join protection
[ ] leave community
[ ] community post authorization
[ ] create conversation
[ ] prevent duplicate conversation
[ ] send message
[ ] retrieve messages
[ ] edit own message
[ ] reject editing another user's message
[ ] delete own message
[ ] reject deleting another user's message
[ ] notification creation
[ ] notification list
[ ] unread count
[ ] mark read
[ ] mark all read
[ ] profile privacy
[ ] profile posts visibility
[ ] community visibility
[ ] avatar upload
[ ] avatar deletion
[ ] media persistence
[ ] WebSocket authentication
[ ] WebSocket message delivery
[ ] WebSocket notification delivery
[ ] CORS
[ ] migrations
```

# Final Architecture Target

The finished system should behave like:

```text
                     ┌─────────────────────┐
                     │      Next.js        │
                     │       Vercel        │
                     └──────────┬──────────┘
                                │
                     HTTPS REST │
                                ▼
                     ┌─────────────────────┐
                     │      FastAPI        │
                     │      Backend        │
                     └──────────┬──────────┘
                                │
                ┌───────────────┼────────────────┐
                │               │                │
                ▼               ▼                ▼
             PostgreSQL      Redis*          Object Storage*
                                │
                                │
                                ▼
                          WebSocket events

* Redis is needed when realtime connections span
  multiple workers/instances.
* Object storage is recommended for persistent
  production media.
```

The key principle is:

```text
PostgreSQL = source of truth
FastAPI    = business logic + authorization
WebSocket  = realtime delivery
Frontend   = presentation + client state
Object     = persistent media
```
