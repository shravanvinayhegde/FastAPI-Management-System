# 🚀 FEATURE SPECIFICATION

# Communities + Direct Messages + Real-Time Notifications

Implement **Communities** and **1-to-1 Direct Messages (DMs)** in the existing FastAPI Management System.

The implementation must extend the existing architecture rather than rewrite it.

The platform should be **Reddit-inspired and community-driven**, with private 1-to-1 communication.

---

# 1. PRODUCT MODEL

The primary social structure is:

```text
User
 ├── follows → User
 ├── joins → Community
 │              ↓
 │            Posts
 │              ↓
 │          Comments
 │              ↓
 │            Votes
 │
 └── messages → User
                  ↓
             Conversation
                  ↓
               Messages
```

Communities are the primary content-discovery mechanism.

User-to-user following remains a secondary relationship.

Do not turn the platform into an Instagram clone.

Do not ask users for a reason/intent when following someone.

---

# 2. ENGINEERING RULE

Before writing code:

1. Inspect the existing repository.
2. Identify the existing models.
3. Identify the existing Pydantic schemas.
4. Identify the existing routers.
5. Identify authentication dependencies.
6. Identify database/session configuration.
7. Identify existing service/repository patterns.
8. Identify existing tests.
9. Inspect existing Alembic migrations.
10. Follow the existing project conventions.

Do not create a parallel architecture.

Do not refactor unrelated code.

Make the smallest coherent change required.

---

# 3. COMMUNITY SYSTEM

## 3.1 Community Model

Create a `Community` entity.

Suggested fields:

```text
id
name
slug
description
creator_id
created_at
updated_at
```

Requirements:

* `creator_id` → `users.id`
* Unique community name according to product requirements
* Unique URL-safe `slug`
* Appropriate indexes
* Consistent timezone-aware timestamps

Do not store member IDs in arrays or JSON.

---

# 4. COMMUNITY MEMBERSHIP

Implement:

```text
User ↔ Community
```

using a many-to-many association.

Suggested table:

```text
community_members
-----------------
user_id
community_id
joined_at
```

Requirements:

```text
FOREIGN KEY user_id → users.id
FOREIGN KEY community_id → communities.id

UNIQUE(user_id, community_id)
```

Add indexes appropriate for:

```text
user_id
community_id
```

A user must not be able to join the same community twice.

The database must enforce this as well as application logic.

---

# 5. COMMUNITY CRUD

Implement:

```http
POST   /communities
GET    /communities
GET    /communities/{community_id}
```

Required operations:

* Create community
* Retrieve community
* List communities
* Pagination

Creator identity must come from the authenticated JWT user.

Never accept the current user's identity blindly from the request body.

---

# 6. COMMUNITY MEMBERSHIP API

Implement:

```http
POST   /communities/{community_id}/join
DELETE /communities/{community_id}/join

GET    /communities/{community_id}/members
GET    /users/me/communities
```

Requirements:

* Authentication required
* Membership authorization enforced
* Pagination on collection endpoints
* Duplicate joins handled safely
* Leaving a community the user has not joined must have defined behavior

Suggested behavior:

```text
Join existing membership → 409 Conflict or idempotent success
Leave non-member → 404/204 depending on existing API conventions
```

Follow the repository's established error semantics.

---

# 7. COMMUNITY AUTHORIZATION

The creator owns the community.

Never trust:

```text
creator_id
```

from an untrusted request.

Use:

```text
authenticated_user.id
```

for creator identity.

Privileged community operations must verify ownership/authorization.

Do not rely on frontend controls.

---

# 8. COMMUNITY NAME / SLUG EDGE CASES

Handle:

```text
empty name
whitespace-only name
name too long
description too long
duplicate name
duplicate slug
case variations
special characters
invalid slug
community does not exist
creator does not exist
```

Do not silently normalize data in a way that creates unexpected collisions.

---

# 9. POST ↔ COMMUNITY

Extend the existing `Post` model to support:

```text
community_id → communities.id
```

A post can belong to a community according to the product rules.

Do not break existing post functionality.

Community post retrieval:

```http
GET /communities/{community_id}/posts
```

must support pagination.

If the existing post creation endpoint is the preferred architecture, integrate `community_id` there instead of unnecessarily creating duplicate post endpoints.

---

# 10. COMMUNITY POST SORTING

Support:

```http
GET /communities/{community_id}/posts?sort=new
GET /communities/{community_id}/posts?sort=top
GET /communities/{community_id}/posts?sort=hot
```

Definitions:

### new

Newest posts first.

### top

Highest vote score/popularity.

### hot

Combination of popularity and recency.

Initial ranking must be deterministic and explainable.

Do not introduce ML for the initial implementation.

Do not fetch a large dataset and sort it in Python.

Prefer database-side ordering and aggregation.

---

# 11. COMMUNITY DELETION

Do not automatically cascade-delete all related content.

Before implementing deletion, explicitly determine behavior for:

```text
Community
 ├── Members
 ├── Posts
 ├── Comments
 ├── Votes
 └── Notifications
```

Use:

```text
CASCADE
SET NULL
RESTRICT
SOFT DELETE
```

only where appropriate.

Never delete unrelated users or unrelated resources.

---

# 12. DIRECT MESSAGING

Implement **private 1-to-1 asynchronous messaging**.

Initial scope:

```text
User A ↔ User B
```

Do not implement group chats initially.

Do not build a WhatsApp/Discord clone.

---

# 13. CONVERSATION MODEL

Create:

```text
conversations
```

Suggested fields:

```text
id
created_at
updated_at
```

A conversation represents a private thread.

---

# 14. CONVERSATION MEMBERSHIP

Create:

```text
conversation_members
--------------------
conversation_id
user_id
joined_at
```

Requirements:

* Foreign keys
* Unique `(conversation_id, user_id)`
* Appropriate indexes

For the initial implementation, every conversation must contain exactly two users.

Do not expose arbitrary membership manipulation through public APIs.

---

# 15. DUPLICATE CONVERSATIONS

There must be only one active 1-to-1 conversation between a pair of users.

This must work for:

```text
A → B
B → A
A → B again
```

and also concurrent requests:

```text
Request 1: A creates conversation with B
Request 2: B creates conversation with A
```

Do not rely only on:

```text
SELECT
IF NOT EXISTS
INSERT
```

because that is race-prone.

Use a safe transactional/database strategy.

---

# 16. SELF-CONVERSATION

Prevent:

```text
A → A
```

Self-messaging is not supported unless explicitly required later.

Return an appropriate client error.

---

# 17. CONVERSATION API

Implement:

```http
POST /conversations
GET  /conversations
GET  /conversations/{conversation_id}
GET  /conversations/{conversation_id}/messages
```

Creating a conversation should:

1. Authenticate current user.
2. Validate target user.
3. Prevent self-conversation.
4. Search for existing conversation.
5. Return existing conversation if appropriate.
6. Otherwise create one safely.

---

# 18. MESSAGE MODEL

Create:

```text
messages
--------
id
conversation_id
sender_id
content
created_at
updated_at
```

Requirements:

```text
conversation_id → conversations.id
sender_id → users.id
```

Messages must be individual rows.

Do not store all messages as JSON inside `conversations`.

---

# 19. SEND MESSAGE API

Implement:

```http
POST /conversations/{conversation_id}/messages
```

Example request:

```json
{
  "content": "Hello!"
}
```

The server must determine:

```text
sender_id = authenticated_user.id
```

Never trust `sender_id` supplied by the client.

---

# 20. MESSAGE VALIDATION

Reject:

```text
empty content
whitespace-only content
content exceeding configured maximum
malformed payload
unauthorized conversation
nonexistent conversation
```

Set a reasonable maximum message size.

Do not accept arbitrarily huge payloads.

---

# 21. MESSAGE ACCESS CONTROL

A user can read a conversation only if they are a member.

A user can send a message only if they are a member.

A user can edit/delete a message only if the product's authorization rules allow it.

For message editing:

```text
authenticated user
        ↓
message exists
        ↓
authenticated user == sender
        ↓
allow
```

Never rely on the frontend.

---

# 22. MESSAGE API

Implement where appropriate:

```http
POST   /conversations/{conversation_id}/messages
GET    /conversations/{conversation_id}/messages
PATCH  /messages/{message_id}
DELETE /messages/{message_id}
```

All history endpoints must be paginated.

---

# 23. MESSAGE PAGINATION

Never return an entire conversation history.

Example:

```http
GET /conversations/{conversation_id}/messages?limit=50&offset=0
```

Cap maximum page size.

Handle:

```text
negative limit
negative offset
limit = 0
very large limit
empty result
page beyond available messages
deleted messages
```

For deterministic ordering use:

```text
ORDER BY created_at ASC, id ASC
```

or the appropriate reverse ordering for the API.

Do not rely solely on timestamps because multiple records can share the same timestamp.

---

# 24. MESSAGE IDEMPOTENCY

Network requests can be retried.

Consider a client-generated message/request identifier for message creation if duplicate-message retries are a realistic concern.

Example:

```text
Client sends message
      ↓
Server stores it
      ↓
Response lost
      ↓
Client retries
```

The implementation should have a defined strategy to avoid accidental duplicate messages.

Do not add complex infrastructure unless required.

---

# 25. READ / UNREAD STATE

Track read state at conversation-member level.

Suggested:

```text
conversation_members
--------------------
last_read_at
```

Unread messages can be determined from:

```text
message.created_at > last_read_at
```

Provide appropriate APIs, for example:

```http
POST /conversations/{conversation_id}/read
```

and/or:

```http
GET /notifications/unread-count
```

Do not create a separate row for every read state unless there is a real requirement.

---

# 26. REAL-TIME ARCHITECTURE

Real-time delivery is required.

Use:

```text
FastAPI WebSocket
```

for:

* New messages
* New notifications
* Future real-time events

REST remains responsible for persistent CRUD and history retrieval.

Use:

```text
REST
 ↓
Persist to PostgreSQL
 ↓
Commit transaction
 ↓
Push event through WebSocket
```

The WebSocket is a delivery mechanism.

PostgreSQL is the source of truth.

---

# 27. WEBSOCKET ENDPOINT

Create one authenticated real-time channel, following existing API conventions.

Example:

```text
WS /ws
```

or:

```text
WS /ws/events
```

The exact route must match repository conventions.

Lifecycle:

```text
CONNECT
 ↓
AUTHENTICATE
 ↓
REGISTER USER CONNECTION
 ↓
WAIT FOR EVENTS
 ↓
SEND EVENTS
 ↓
DISCONNECT
 ↓
CLEANUP
```

---

# 28. WEBSOCKET AUTHENTICATION

Never derive identity from:

```text
/ws/{user_id}
```

The authenticated JWT/token must determine the user.

Reject:

```text
missing token
invalid token
expired token
malformed authentication
deleted/inactive user
```

where applicable.

---

# 29. CONNECTION MANAGER

Support multiple connections per user.

Conceptually:

```python
user_id -> set[WebSocket]
```

A user can be connected through:

```text
Browser
Mobile
Multiple tabs
Multiple devices
```

A failed connection must not prevent delivery to other active connections.

Clean up dead connections.

Never retain stale sockets indefinitely.

---

# 30. REAL-TIME MESSAGE FLOW

Correct flow:

```text
User A
   ↓
POST /conversations/{id}/messages
   ↓
Authenticate
   ↓
Verify conversation membership
   ↓
Validate message
   ↓
Persist message
   ↓
Commit transaction
   ↓
Push NEW_MESSAGE event
   ↓
User B receives message immediately
```

Do not send the real-time event before the transaction succeeds.

---

# 31. REAL-TIME EVENT FORMAT

Use predictable structured JSON.

Example:

```json
{
  "type": "new_message",
  "conversation_id": 123,
  "message": {
    "id": 456,
    "sender_id": 10,
    "content": "Hello!",
    "created_at": "2026-09-05T12:30:00Z"
  }
}
```

Do not send raw SQLAlchemy model instances through WebSockets.

Serialize through defined schemas.

---

# 32. NOTIFICATION MODEL

Create persistent notifications.

Suggested:

```text
notifications
-------------
id
recipient_id
actor_id
type
entity_type
entity_id
payload
is_read
created_at
```

Notification metadata must be validated/structured.

Possible types:

```text
NEW_FOLLOWER
NEW_MESSAGE
MESSAGE_REQUEST
POST_COMMENT
COMMENT_REPLY
COMMUNITY_EVENT
```

Keep the design extensible.

---

# 33. REAL-TIME NOTIFICATION FLOW

For an event such as a new follower:

```text
User A follows User B
        ↓
Persist relationship
        ↓
Persist notification for B
        ↓
Commit
        ↓
Push notification through WebSocket
```

For a message:

```text
Message persisted
        ↓
Notification/event created if required
        ↓
Commit
        ↓
WebSocket push
```

Do not depend on WebSocket delivery for persistence.

---

# 34. OFFLINE USERS

If the recipient is offline:

```text
Database:
    message = persisted
    notification = persisted

WebSocket:
    unavailable
```

When the user reconnects:

```text
Authenticate
   ↓
Synchronize unread notifications
   ↓
Synchronize unread/message state
```

No important persistent event should be lost because of a disconnected socket.

---

# 35. RECONNECTION

Handle:

```text
network loss
browser sleep
server restart
temporary connection failure
mobile network changes
```

When reconnecting:

```text
CONNECT
 ↓
AUTHENTICATE
 ↓
REGISTER
 ↓
SYNC PERSISTED STATE
```

Do not assume every event was successfully delivered in real time.

---

# 36. WEBSOCKET FAILURE

Scenario:

```text
Database commit succeeds
        ↓
WebSocket send fails
```

Expected behavior:

* Persisted data remains valid.
* Dead socket is removed.
* Failure is logged appropriately.
* Client can recover through synchronization.
* Request must not crash the application.

Do not retry indefinitely inside the request.

---

# 37. SERVER RESTART

WebSocket connections naturally disappear after process restart.

Persistent messages and notifications must remain in PostgreSQL.

Clients should reconnect and synchronize state.

Do not rely on in-memory state for persistence.

---

# 38. NOTIFICATION API

Implement:

```http
GET  /notifications
POST /notifications/{notification_id}/read
POST /notifications/read-all
GET  /notifications/unread-count
```

Use pagination.

Do not return thousands of notifications in one response.

---

# 39. NOTIFICATION AUTHORIZATION

A user may only retrieve notifications belonging to themselves.

A user must not be able to:

```text
read another user's notifications
mark another user's notifications as read
```

The recipient must be derived from authentication.

---

# 40. NOTIFICATION EDGE CASES

Handle:

```text
recipient deleted
actor deleted
target post deleted
target comment deleted
target user deleted
duplicate event
notification already read
notification marked read twice
empty notification list
large notification list
```

The API must not crash because an old target entity was deleted.

---

# 41. MESSAGE REQUESTS

Optional but recommended if compatible with the product.

When a user messages someone they have no prior conversation with:

```text
PENDING
```

can be used as a message-request state.

Flow:

```text
A → message B
      ↓
Message Request
      ↓
B accepts
      ↓
Conversation becomes active
```

Do not implement this if it significantly complicates the initial architecture.

Core messaging takes priority.

---

# 42. SECURITY REQUIREMENTS

Protect all private resources.

Test:

```text
User A → own resource
User A → User B resource
Unauthenticated → resource
Invalid JWT → resource
Expired JWT → resource
```

Especially test:

```text
Conversations
Messages
Notifications
Community management
Membership
Follows
```

Never expose:

```text
password hashes
JWT tokens
private messages
private conversation data
database credentials
internal stack traces
```

---

# 43. DATABASE TRANSACTIONS

Use transactions for logically related modifications.

Examples:

```text
Create conversation
+
Create two conversation members
```

```text
Send message
+
Update conversation timestamp
```

```text
Follow user
+
Create notification
```

Define transaction boundaries clearly.

Do not allow partial state.

---

# 44. CONCURRENT OPERATIONS

The implementation must handle:

```text
Two simultaneous follows
Two simultaneous joins
Two simultaneous conversation creations
Two simultaneous messages
Follow + unfollow race
Join + leave race
Read state updates from multiple devices
```

Use database constraints and appropriate transactions.

Do not rely exclusively on:

```text
SELECT → IF NOT EXISTS → INSERT
```

for race-sensitive uniqueness.

---

# 45. DATABASE FAILURE

Handle:

```text
database unavailable
connection timeout
constraint violation
transaction rollback
deadlock where relevant
```

Never return success after a failed transaction.

Do not hide database exceptions silently.

---

# 46. PAGINATION

All large collections must be paginated:

```text
Communities
Members
User communities
Community posts
Conversations
Messages
Notifications
Followers
Following
```

Never fetch unbounded data.

Set a reasonable maximum page size.

---

# 47. PERFORMANCE

Avoid:

```text
N+1 queries
unbounded SELECT
Python-side large-data sorting
loading complete conversations into memory
loading complete follower lists
loading complete community memberships
```

Use appropriate:

```text
SQL JOIN
SQL aggregation
selectinload
joinedload
indexes
pagination
```

depending on the query.

Inspect generated SQL for complex queries.

---

# 48. MULTI-INSTANCE WEBSOCKET LIMITATION

An in-memory connection manager is acceptable for a single application instance.

If the deployment later runs multiple FastAPI instances:

```text
Client A → Server 1
Client B → Server 2
```

an event generated on Server 1 cannot automatically reach Client B through Server 2's local memory.

Do not falsely claim horizontal WebSocket scalability.

If multiple instances become a real deployment requirement, introduce shared event propagation such as Redis Pub/Sub or another suitable transport.

Do not add Redis solely for the initial implementation.

---

# 49. TESTING REQUIREMENTS

Create tests for all new functionality.

## Communities

```text
✓ create
✓ retrieve
✓ list
✓ duplicate name
✓ duplicate slug
✓ join
✓ duplicate join
✓ leave
✓ leave when not a member
✓ list members
✓ list user communities
✓ invalid community
✓ unauthorized management
✓ pagination
✓ community posts
```

## Conversations

```text
✓ create
✓ existing conversation reuse
✓ A → B and B → A resolve to same conversation
✓ self-conversation rejected
✓ invalid target user
✓ deleted target user
✓ duplicate concurrent creation
✓ list conversations
✓ unauthorized access
```

## Messages

```text
✓ send
✓ receive
✓ empty content rejected
✓ oversized content rejected
✓ unauthorized send
✓ unauthorized read
✓ unauthorized edit
✓ unauthorized delete
✓ pagination
✓ deterministic ordering
✓ duplicate/retry behavior
✓ deleted conversation
✓ deleted sender/recipient
```

## Read state

```text
✓ unread count
✓ mark read
✓ mark already-read conversation
✓ unauthorized read-state modification
✓ multiple-device updates
✓ stale timestamp protection
```

## Notifications

```text
✓ notification persisted
✓ real-time delivery
✓ offline persistence
✓ unread count
✓ mark read
✓ mark all read
✓ duplicate event handling
✓ deleted target handling
✓ unauthorized access
```

## WebSockets

```text
✓ authenticated connection
✓ unauthenticated connection rejected
✓ invalid token rejected
✓ expired token rejected
✓ multiple connections
✓ disconnect cleanup
✓ failed socket delivery
✓ reconnect
✓ offline recipient
✓ server restart recovery
✓ private events not leaked
```

---

# 50. ALEMBIC REQUIREMENTS

Every schema change requires Alembic.

Implementation order:

```text
SQLAlchemy model
        ↓
Alembic migration
        ↓
Review migration
        ↓
Apply migration
        ↓
Run tests
```

Before adding unique constraints, check existing data for duplicates.

Never blindly trust generated migrations.

---

# 51. API CONTRACT

Use Pydantic response models.

Do not return raw SQLAlchemy objects.

Do not expose unnecessary internal fields.

Use consistent error responses according to the existing project.

Preserve existing API contracts unless a breaking change is explicitly required.

---

# 52. DEPENDENCIES

Do not automatically introduce:

```text
Redis
Celery
RabbitMQ
Kafka
Elasticsearch
Kubernetes
Microservices
```

Use existing infrastructure first.

Only add external infrastructure when a real requirement exists.

---

# 53. IMPLEMENTATION ORDER

Implement in this order:

```text
1. Community model
2. Community membership
3. Community CRUD
4. Post ↔ Community integration
5. Community post queries
6. Conversation model
7. Conversation membership
8. Message model
9. Conversation APIs
10. Message APIs
11. Read/unread state
12. Notification persistence
13. WebSocket connection manager
14. Real-time message delivery
15. Real-time notification delivery
16. Reconnection/offline synchronization
17. Tests
18. Alembic migration review
19. Performance review
20. Security review
```

Do not implement advanced message requests or multi-instance event infrastructure before the core system is stable.

---

# 54. FINAL ACCEPTANCE CRITERIA

The feature is complete only when:

## Communities

[ ] Communities are persisted in PostgreSQL
[ ] Membership is many-to-many
[ ] Duplicate memberships are prevented
[ ] Community authorization works
[ ] Posts can belong to communities
[ ] Community posts are paginated
[ ] Community sorting works

## DMs

[ ] Conversations are persisted
[ ] Conversations are strictly 1-to-1
[ ] Duplicate conversations are prevented
[ ] Self-conversations are rejected
[ ] Messages are persisted individually
[ ] Sender identity comes from JWT
[ ] Conversation membership is enforced
[ ] Message authorization works
[ ] Message history is paginated
[ ] Read/unread state works

## Real Time

[ ] WebSocket authentication works
[ ] Multiple connections per user are supported
[ ] New messages are delivered instantly
[ ] Notifications are delivered instantly
[ ] Persistent state exists even when user is offline
[ ] Reconnection works
[ ] Dead connections are cleaned up
[ ] WebSocket failures do not corrupt database state
[ ] Private events cannot leak to other users

## Database

[ ] Foreign keys correct
[ ] Unique constraints correct
[ ] Check constraints correct
[ ] Appropriate indexes exist
[ ] Transactions are correct
[ ] Race conditions considered
[ ] Alembic migrations created and reviewed

## Security

[ ] Authentication enforced
[ ] Authorization enforced
[ ] Client cannot impersonate another user
[ ] Private messages protected
[ ] Private notifications protected
[ ] No sensitive data logged
[ ] No secrets committed

## Performance

[ ] Pagination implemented
[ ] N+1 avoided
[ ] Large data is not loaded into memory unnecessarily
[ ] Feed/community sorting uses database queries
[ ] WebSocket cleanup works

## Testing

[ ] Happy paths tested
[ ] Failure paths tested
[ ] Authorization tested
[ ] Concurrency cases tested where practical
[ ] Offline/reconnection behavior tested
[ ] Regression tests pass
[ ] Relevant tests were actually executed

---

# FINAL ARCHITECTURE

```text
                         USER
                    ┌──────┼──────┐
                    │      │      │
                 FOLLOW   JOIN     DM
                    │      │       │
                    │  COMMUNITY   │
                    │      │       │
                    │     POSTS  CONVERSATION
                    │      │       │
                    │  COMMENTS   MESSAGES
                    │      │       │
                    │    VOTES      │
                    │      │       │
                    └──────┴───────┘
                           │
                      NOTIFICATIONS
                           │
                       WEBSOCKET
                           │
                    REAL-TIME CLIENT
```

## Core reliability principle

```text
                    EVENT
                      │
             ┌────────┴────────┐
             ↓                 ↓
        PostgreSQL          WebSocket
         PERSIST             PUSH
             │                 │
             ↓                 ↓
       OFFLINE RECOVERY   INSTANT DELIVERY
```

**PostgreSQL is the source of truth.
WebSocket is the real-time delivery mechanism.**

Never sacrifice persistence, authorization, or data integrity for real-time behavior.
