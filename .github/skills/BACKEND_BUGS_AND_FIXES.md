# VoteFlow Backend — Bugs Found and Fixed

**Repo:** `shravanvinayhegde/FastAPI-Management-System`
**Stack:** FastAPI 0.135.3, SQLAlchemy 2.0.49, Starlette 1.0.0, Alembic, PostgreSQL

## How this was verified

Every fix below was tested against the real code, not read-and-guessed:

1. Cloned the repo and installed the **exact pinned versions** from `requirements.txt` into a clean virtualenv.
2. Installed a real **PostgreSQL 16** server and ran `alembic upgrade head` against it — the actual migration chain, not a mocked ORM.
3. Wrote a 27-check verification script (`verify_backend.py`) that exercises the API through `TestClient`: register/login flows, follow graphs, drafts, media uploads (including a real 14000×14000 decompression-bomb PNG and real MP4/WebM byte signatures), WebSocket connections, and production-config guards run in a subprocess with `RENDER=true`.
4. Ran that script **before** touching any code (baseline) and **after** every fix, on a freshly reset database each time.
5. Ran the repository's own existing test suites (`tests/`, `test_backend.py` — 15 tests) after all fixes to check for regressions.

**Result: baseline 1/27 passing → after fixes 27/27 passing. All 15 pre-existing tests still pass.**

Diff stats: 12 files changed, 282 insertions, 46 deletions.

---

## Bugs, by severity

### Critical

#### 1. Followers and following lists were swapped, and crashed on users with no avatar
**File:** `routers/profile.py` — `_profile_users()`
**Symptom:** `GET /users/{username}/followers` returned who that user *follows*, and vice versa. Worse, if any user in the list had no avatar uploaded, the response failed with a 500, because `PublicUser.avatar_url` was typed as a required `str` while the database column is nullable.

**Root cause:** the helper's `column`/`join_column` variables were assigned backwards relative to their names, and it returned raw ORM objects instead of constructing the response schema with a computed default avatar.

**Fix:**
```python
def _profile_users(db, target_id, following, skip, limit):
    if following:
        join_column = models.user_follows.c.following_id
        filter_column = models.user_follows.c.follower_id
    else:
        join_column = models.user_follows.c.follower_id
        filter_column = models.user_follows.c.following_id
    users = db.query(models.User).join(models.user_follows, join_column == models.User.id) \
        .filter(filter_column == target_id)...all()
    return [schemas.PublicUser(..., avatar_url=_avatar_url(u)) for u in users]
```
**Verified:** `dave.followers=['carol']`, `carol.following=['dave']`, both empty lists resolve correctly instead of 500ing.

#### 2. Messaging someone you already have a conversation with returned a 500
**File:** `routers/chat.py` — `create_conversation()`
**Symptom:** The first "Message" click worked; the second (or any later one, since the conversation already exists) crashed with a 500, because the endpoint returned the bare SQLAlchemy `Conversation` object, which doesn't satisfy the `ConversationOut` response model (missing `other_user`, `last_message`, `unread_count`).

**Fix:** route the existing-conversation branch through the same `_conversation_response()` helper used everywhere else.
**Verified:** first call → 201, second call → 201 with the same conversation id.

#### 3. Emails were exposed to any authenticated (and in one case, *any*) user
**Files:** `routers/user.py`, `routers/community.py`, `app/schemas.py`
**Symptom:** `GET /users/`, `GET /users/{id}`, and the followers/following lists all returned full email addresses to any logged-in user browsing someone else's profile. `GET /communities/{id}/members` returned emails to **anonymous** requests — no login required at all.

**Fix:** added a `UserPublic` schema (same as `UserOut` minus `email`) and switched every listing/lookup endpoint to it. `/communities/{id}/members` now requires authentication.
**Verified:** `/users/` returns 0 emails; `/communities/{id}/members` returns 401 anonymously and no emails when authenticated.

#### 4. 500 errors didn't carry CORS headers, so the frontend showed "can't reach the API" instead of the real error
**File:** `app/main.py`
**Symptom:** Any unhandled server exception (not a normal `HTTPException`) produced a response with no `Access-Control-Allow-Origin` header. The browser then reports a CORS failure to the frontend's JS, hiding whatever the actual 500 was about.

**Root cause (the interesting one):** the obvious fix — `@app.exception_handler(Exception)` — doesn't work. Starlette special-cases a handler registered for the bare `Exception` class and wires it into `ServerErrorMiddleware`, which sits **outside** `CORSMiddleware` in the stack (confirmed by reading Starlette 1.0's own `build_middleware_stack()`). A response built there never passes back through `CORSMiddleware`, so it can never get the header, no matter what the handler does.

**Fix:** a plain ASGI middleware, registered **before** `CORSMiddleware` so it ends up *inside* it:
```python
class UnhandledErrorMiddleware:
    async def __call__(self, scope, receive, send):
        ...
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception(...)
            if not response_started:
                await JSONResponse(status_code=500, content={"detail": "Internal Server Error"})(scope, receive, send)

app.add_middleware(UnhandledErrorMiddleware)   # added first -> ends up innermost
app.add_middleware(CORSMiddleware, ...)         # added second -> ends up outermost, wraps the above
```
**Verified:** a forced `RuntimeError` now returns `ACAO: http://localhost:3000` on the 500 response (was `None` before, and still `None` after the first, exception-handler-based attempt).

#### 5. Production could boot with an insecure default and silently fall back to a wiped-on-restart database
**Files:** `app/config.py`, `app/database.py`
**Symptom:** if Render's environment was missing `SECRET_KEY`, the app booted anyway with the hardcoded default `"CHANGE_ME"`, letting anyone forge login tokens. Similarly, a missing `DATABASE_URL`/`DATABASE_*` config silently fell back to a local SQLite file, which Render wipes on every deploy — so all data would vanish without anyone noticing until the site "reset itself."

**Fix:** both modules now check `os.getenv("RENDER")` (Render sets this automatically) and raise `RuntimeError` at import time if the dangerous default is still in place, rather than starting up in a broken state.
**Verified:** a subprocess import with `RENDER=true` + `secret_key=CHANGE_ME` exits non-zero; same for missing DB config.

### High

#### 6. `GET /posts/` had no `ORDER BY` and no pagination bounds
**File:** `routers/post.py`
**Symptom:** without an explicit order, Postgres doesn't guarantee row order across requests, so paging (`skip`/`limit`) could repeat or skip posts, and an edited post could visibly jump position. `limit=-1` and `skip=-5` both crashed with a 500 instead of a clean validation error.
**Fix:** `Query(..., ge=1, le=100)` / `Query(0, ge=0)` on both parameters, plus `.order_by(created_at.desc(), id.desc())`.
**Verified:** `limit=-1`, `skip=-5`, `limit=100000` all now return 422; feed order is stable after an edit.

#### 7. Editing a post could silently re-publish a draft
**File:** `routers/post.py`, `app/schemas.py`
**Symptom:** `PUT /posts/{id}` reused the same schema as post creation, where `published` defaults to `True`. Sending `{"title": "..."}` alone (as any partial-edit form would) silently flipped a draft back to published, because the omitted field was read as "yes, publish this."
**Fix:** new `PostUpdate` schema where `published` defaults to `None`; the handler only touches the column when the caller actually sent a value.
**Verified:** editing a draft's title without mentioning `published` leaves it unpublished.

#### 8. Owners couldn't view their own unpublished drafts; strangers could reply to them
**File:** `routers/post.py`
**Symptom:** `GET /posts/{id}` filtered out unpublished posts unconditionally — even for the post's own owner. Meanwhile `POST /posts/{id}/replies` had no such check at all, so any logged-in user could discover and comment on someone else's draft.
**Fix:** a draft is now visible to its owner only; replies are blocked on any post that isn't published unless you own it.
**Verified:** owner gets 200 on their own draft, anonymous gets 404; a reply attempt on someone else's draft returns 404.

#### 9. Image/video decompression bombs crashed with a 500 instead of a clean rejection
**Files:** `routers/post.py`, `routers/profile.py`
**Symptom:** uploading a small PNG that decodes to an enormous bitmap (e.g. 14000×14000) raised Pillow's `Image.DecompressionBombError` — a plain `Exception`, not an `OSError` — which wasn't in the existing `except` clause, so it surfaced as an unhandled 500.
**Fix:** added `Image.DecompressionBombError` to the caught exception types in both the post-media and avatar upload paths.
**Verified:** the same 14000×14000 upload now returns 422.

#### 10. Video filenames had a double dot (`abc..mp4`)
**File:** `routers/post.py`
**Root cause:** the extension variable already included its leading dot for videos (`.mp4`) but not for images (`jpg`), and the filename template unconditionally added another dot.
**Fix:** `f"{uuid.uuid4().hex}.{extension.lstrip('.')}"` — works for both cases.
**Verified:** stored filenames are now `<uuid>.mp4`, not `<uuid>..mp4`.

#### 11. Non-ASCII community names were rejected; slug routes returned the wrong status
**File:** `routers/community.py`
**Symptom:** a community named "मराठी" reduced to an empty slug under the old ASCII-only `[^a-z0-9]+` regex and was rejected with a confusing 422. Separately, `GET /communities/{id}` had no type converter, so a non-numeric path segment like `/communities/zincs` (a naive but plausible frontend call) triggered a 422 from failed int coercion instead of a clean 404.
**Fix:** Unicode-aware slugify using Python 3's Unicode-aware `\w`, and `:int` converters added to every `community_id` route.
**Verified:** the Marathi name now gets a real slug and resolves via `/communities/by-slug/...`; `/communities/zincs` (non-numeric) now 404s.

#### 12. WebSocket connections held a pooled database connection for their entire lifetime
**File:** `routers/chat.py`
**Symptom:** the events websocket opened a `SessionLocal()` only to check the token once, then kept that session (and its pooled connection) open for as long as the socket stayed connected — potentially hours for an idle "just listening" client, tying up a connection pool slot for nothing.
**Fix:** the session is now opened, used to verify the user, and closed immediately — before entering the `receive_text()` loop.
**Verified:** with one idle socket connected, `engine.pool.checkedout()` is 0.

### Medium

#### 13. Registration accepted invalid emails and let the same address register twice via case
**File:** `app/schemas.py`, `routers/auth.py`
**Symptom:** `email: str` (not Pydantic's `EmailStr`) meant `"notanemail"` was accepted as a valid registration. Because nothing normalized case, `Alice@x.com` and `alice@x.com` could both register as separate accounts, and login was case-sensitive (a user typing their email differently than they registered would be told their password was wrong).
**Fix:** `EmailStr` (added `email-validator` to `requirements.txt`) with a validator that lowercases/strips on the way in; login now compares with `func.lower()`.
**Verified:** `"notanemail"` → 422; a second registration differing only in case → 409; login with `ALICE@EXAMPLE.COM` → 200.

#### 14. No minimum password length
**File:** `app/schemas.py`
**Fix:** `Field(min_length=8, max_length=72)`.
**Verified:** an 3-character password → 422.

#### 15. Search matched on `%` and `_` as SQL wildcards
**File:** `routers/post.py`
**Symptom:** searching for a title containing a literal `%` or `_` (e.g. "50% off") matched far more posts than intended, because those characters were passed straight into `ILIKE` unescaped.
**Fix:** escape `\`, `%`, and `_` in the search term and pass `escape="\\"` to `ilike()`.
**Verified:** searching for a literal `%` now returns only the post that actually contains one.

#### 16. The main feed fired one extra SQL query per post (N+1)
**File:** `routers/post.py`
**Symptom:** `owner` and `media` are lazy-loaded relationships; returning a page of posts without eager loading fired one additional query per post per relationship as the response model serialized them.
**Fix:** a `_hydrate_posts()` helper does one extra bulk query (`joinedload(owner)` + `selectinload(media)`) for the whole page, regardless of page size.
**Verified:** an 8-post page now takes 5 SQL statements total (was scaling linearly with post count before).

#### 17. `updated_at` never actually updated
**File:** `app/models.py`
**Symptom:** every table's `updated_at` column had a `server_default` but no `onupdate`, so it silently stayed frozen at creation time forever — including on `users`, where editing your profile bio never touched it.
**Fix:** `onupdate=func.now()` added to all five `updated_at` columns (this is a client-side SQLAlchemy directive, not DDL — no migration needed).
**Verified:** `created_at != updated_at` after a profile edit.

#### 18. Notifications for a conversation never cleared when you read it
**File:** `routers/chat.py`
**Symptom:** opening a DM thread updated `last_read_at` on the membership row, but the corresponding `NEW_MESSAGE` notification stayed unread forever, so the unread-count badge never went down for messages specifically.
**Fix:** `mark_conversation_read()` now also marks matching `NEW_MESSAGE` notifications as read.
**Verified:** unread count drops from 1 to 0 after marking a conversation read.

#### 19. The feed had no server-side "Top" sort, even though the frontend has a Top tab
**File:** `routers/post.py`
**Context:** `list_community_posts()` already supported `sort=new|top|hot`, but the main `GET /posts/` feed didn't support sorting at all — the frontend's "Top" tab (see the frontend report) could only re-sort whichever 20 posts happened to already be loaded, never seeing older high-voted posts.
**Fix:** added the same `sort` parameter to the main feed, mirroring the existing, already-working pattern.
**Verified:** `?sort=top` now orders by vote count first.

#### 20. `PostOut` didn't expose whether the current viewer had voted
**File:** `app/schemas.py`, `routers/post.py`
**Fix:** added `voted: Optional[bool]`, populated via one extra bulk query per page when a token is present.
**Verified:** posts the test user voted on come back with `voted: true`.

---

## Known issue, not fixed (pre-existing, unrelated to this round of fixes)

`alembic check` reports drift between `models.py` and the actual migration history on **communities' unique indexes, a couple of `conversation`/`conversations` indexes, `posts.published`'s nullability, and `users.username`'s unique constraint**. This drift **predates this session's changes** — it was present in the original, untouched clone before I fixed anything, and none of the columns/constraints involved were touched by any fix above (the only ORM change was adding `onupdate=func.now()`, which is a Python-side directive with no corresponding DDL, so it cannot have caused this). It doesn't affect runtime correctness — every behavioral check above passes against the actual live schema — but reconciling it properly needs a real audit of the live production database before writing a migration, which is outside the scope of a bug-fix pass like this one. Flagging it so it doesn't surprise anyone who runs `alembic --autogenerate` later.

## How to re-verify

```bash
# from the backend repo root, with the venv from requirements.txt active
alembic upgrade head
python -m pytest tests test_backend.py -q     # 15 passed
python verify_backend.py                      # 27/27 checks (script provided alongside this report)
```
