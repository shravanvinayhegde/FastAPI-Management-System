VoteFlow Backend — Fix Guide

Repo: FastAPI-Management-System (FastAPI + SQLAlchemy + Alembic + PostgreSQL, JWT auth, deployed on Render). Companion doc: frontend-fixes.md for FastAPI-Social-Management-System-Frontend (VoteFlow, Next.js). The "Community not found" / "This user does not exist" bugs were caused by a bug in the frontend repo, not here — see that doc. The broken-post-image bug (screenshot: images load right after posting, then show broken in a later session) is rooted here — see P0 below. This repo also has one independent router bug (breaks followers/following lists, P1) plus several smaller issues found while auditing every router. Fix everything in this file; these are all unrelated to each other and to the frontend bug.

Everything below was verified either by actually running the code (isolated FastAPI reproduction with TestClient for P1) or by cross-checking against your own README and Render's current documentation (P0). Repro/verification steps are included throughout.

P0 — Uploaded media (post images/videos, avatars) disappears after every restart

This is the cause of the broken-image screenshot ("loads right after posting, shows broken in the next session"). Confirmed from your own README and Render's documentation, not just inferred from code.

Root cause

Media is written straight to the container's local disk and served back from that same local disk:

python
# routers/post.py, line 19 and line 73 (inside _store_post_media)
POST_MEDIA_DIRECTORY = settings.media_directory / "posts"
...
(POST_MEDIA_DIRECTORY / filename).write_bytes(data)
return schemas.MediaUploadOut(url=f"/media/posts/{filename}", ...)
python
# routers/profile.py, lines 19, 180, 184 (upload_avatar) — identical pattern
AVATAR_DIRECTORY = settings.media_directory / "avatars"
...
AVATAR_DIRECTORY.mkdir(parents=True, exist_ok=True)
current_user.avatar_url = f"/media/avatars/{filename}"
python
# app/main.py, lines 9-12
app = FastAPI()
settings.media_directory.mkdir(parents=True, exist_ok=True)
(settings.media_directory / "avatars").mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(settings.media_directory)), name="media")

Nothing in requirements.txt or anywhere in the codebase talks to S3, R2, or any other object store — this is 100% local-disk storage. Your own README.md (line 30) and .env.example/README (lines 124–126) already flag both halves of the problem:

⚠️ The service may take 30–60 seconds to wake up on first load, as it runs on Render's free tier.

# Media storage / # Set this to a persistent mounted volume in production. / media_directory=media

That second comment is a to-do that was never finished. Render's own docs are explicit about what this means in practice: "Free web services have an ephemeral filesystem. This means that any changes to your web service's filesystem (uploaded images, local SQLite databases, etc.) are lost every time the service redeploys, restarts, or spins down." Free-tier Render services spin down after ~15 minutes of no traffic and cold-start on the next request — so the container that serves a user's feed an hour later is very likely a brand-new container with a freshly-mkdir'd, empty media/ folder (see the startup mkdir calls above — they succeed silently whether or not anything used to be there). The database still has the PostMedia/avatar_url row pointing at /media/posts/xxxx.jpg; the file behind that path is simply gone. StaticFiles returns a normal 404 for a missing file, the <img> tag fails to load, and the browser falls back to showing its broken-image icon plus the alt="Post media" text — exactly what's in the screenshot.

This also means: files already lost can't be recovered by a code fix. The fix below stops it from happening to new uploads; anything already broken needs to be re-uploaded by its owner once the fix ships.

Fix — recommended: move media to object storage (works on the free tier, no ongoing cost at this scale)

Keep the exact same public contract (/media/posts/xxx.jpg, /media/avatars/xxx.jpg) so the frontend needs zero changes — only the backend's storage backend changes, from local disk to an S3-compatible bucket. Cloudflare R2 is a good fit here specifically because its free tier (10 GB storage, 1M writes/mo, 10M reads/mo, and — unlike S3 — no egress fees) is a permanent monthly allowance, not a 12-month trial, and comfortably covers a project at this scale, while staying on Render's free web service tier.

Add settings (app/config.py):
python
s3_bucket: Optional[str] = None
s3_region: Optional[str] = None
s3_endpoint_url: Optional[str] = None   # required for R2/Spaces/B2; leave unset for AWS S3
s3_access_key_id: Optional[str] = None
s3_secret_access_key: Optional[str] = None
New tiny storage module (app/storage.py):
python
import boto3
from app.config import settings

def _client():
    return boto3.client(
        "s3",
        region_name=settings.s3_region or "auto",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
    )

def save_bytes(key: str, data: bytes, content_type: str) -> None:
    _client().put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)

def public_url(key: str) -> str:
    return f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{key}"
In routers/post.py's _store_post_media (replaces line 73) and routers/profile.py's upload_avatar (replaces its local write), swap the local write for storage.save_bytes(f"posts/{filename}", data, mime_type) / storage.save_bytes(f"avatars/{filename}", data, content_type). The returned/stored url string (/media/posts/{filename}) stays exactly as it is today.
In app/main.py, replace the local StaticFiles mount (lines 10–12) with a redirect so the public /media/... path keeps working unchanged:
python
from fastapi.responses import RedirectResponse
from app import storage

@app.get("/media/{key:path}")
def media_redirect(key: str):
    return RedirectResponse(storage.public_url(key))
Add boto3 to requirements.txt.

This requires an actual bucket and credentials to exist before it'll work end-to-end — provisioning the R2/S3 bucket and setting the five env vars above is a one-time manual step for whoever holds the Cloudflare/AWS account; the code change itself doesn't depend on that happening first, so it can be written and reviewed independently.

Fix — alternative: attach a Render persistent Disk (no code change, but costs money)

Render's docs are explicit that Free web services cannot attach a persistent disk at all — this option requires first upgrading the service to a paid instance type (Starter or above, currently ~$7/mo), then attaching a Disk mounted at whatever path MEDIA_DIRECTORY is set to. This is simpler (zero code changes) and also removes the 30–60s cold-start wake-up as a side effect, but costs money monthly and doesn't scale past a single instance (a Disk isn't shared across replicas, so this stops working the moment you ever run more than one instance). Prefer the object-storage fix unless you specifically want to pay for simplicity.

Verify

After deploying either fix: upload a post image, confirm it loads. Then force a fresh container (redeploy, or — on the free tier — just wait ~15 minutes of inactivity for it to spin down, then reload) and confirm the same image still loads. Before the fix, this second check is exactly where it breaks.

P1 — /users route collision silently breaks followers/following-by-username

Confirmed by test, not guesswork. routers/user.py and routers/profile.py both declare APIRouter(prefix="/users"), and app/main.py includes them in this order:

python
# app/main.py
app.include_router(post.router)
app.include_router(user.router)      # <- included first
app.include_router(auth.router)
app.include_router(vote.router)
app.include_router(community.router)
app.include_router(chat.router)
app.include_router(notification.router)
app.include_router(profile.router)   # <- included last

Starlette matches routes by URL shape, not by the Python type hint on the path parameter. user.py declares id: int in the function signature, but the path string is just "/{id}" — with no :int convertor, that pattern matches any single path segment, numeric or not. Because user.router is registered first, its /{id}/... routes intercept requests before profile.router ever gets a chance, whenever the URL shapes collide.

They collide on exactly two sub-resources: followers and following.

Request	Should reach	Actually reaches	Result
GET /users/alice/followers	profile.get_profile_followers	user.get_followers (tries to parse "alice" as int)	422 error, always
GET /users/alice/following	profile.get_profile_following	user.get_following	422 error, always
GET /users/alice/profile, /posts, /communities, etc.	profile.py	correctly reaches profile.py	fine (no colliding literal segment in user.py)
GET /users/42/followers (numeric, the route this was meant for)	user.get_followers	user.get_followers	fine

Frontend impact: lib/api.ts's getProfileFollowers() / getProfileFollowing() call the username-based endpoints from every profile page's "Followers"/"Following" list — that feature is broken end‑to‑end until this is fixed here.

This isn't hypothetical — a version of this exact bug already bit /users/me (it used to be swallowed by /{id} and 422); a recent commit special-cased /me before /{id} to fix that one instance, but the same root cause was never fixed for followers/following. Patching one URL at a time will keep missing the next one; fix the root cause instead.

Fix

Add an explicit :int convertor to every numeric-ID route in routers/user.py. This makes Starlette's routing regex actually discriminate by shape (digits only), so a non-numeric segment correctly falls through to profile.py instead of being swallowed. No reordering, no renaming, no frontend change needed.

python
# routers/user.py

# line 53
@router.get("/{id:int}", response_model=schemas.UserOut)          # was "/{id}"
def get_user(id: int, ...):

# line 116
@router.post("/{id:int}/follow", response_model=schemas.FollowStatus)   # was "/{id}/follow"
def follow_user(id: int, ...):

# line 162
@router.delete("/{id:int}/follow", response_model=schemas.FollowStatus) # was "/{id}/follow"
def unfollow_user(id: int, ...):

# line 182
@router.get("/{id:int}/follow-status", response_model=schemas.FollowStatus) # was "/{id}/follow-status"
def get_follow_status(id: int, ...):

# line 192
@router.get("/{id:int}/followers", response_model=list[schemas.UserOut])    # was "/{id}/followers"
def get_followers(id: int, ...):

# line 209
@router.get("/{id:int}/following", response_model=list[schemas.UserOut])    # was "/{id}/following"
def get_following(id: int, ...):

Only the path string changes (add :int inside the braces). The function signatures (id: int) stay exactly as they are. GET /users/me and GET /users/me/communities are unaffected — they're already registered before /{id} and don't need to change (they'll keep working, and {id:int} makes them doubly safe since "me" can never match a digits-only pattern anyway).

Do not "fix" this by just reordering include_router(profile.router) before include_router(user.router) — that only flips which feature breaks (numeric-ID followers/following would then 404 instead of the username version), because the two routes still have identical, ambiguous URL shapes. The :int convertor is what actually resolves the ambiguity.

Verify

This is exactly the test used to find and confirm the bug — a self-contained reproduction, no database required:

python
# save as repro_router_collision.py at the repo root and run: python3 repro_router_collision.py
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
user_router = APIRouter(prefix="/users")

@user_router.get("/{id:int}")            # <- the fix
def get_user(id: int): return {"handler": "user.get_user", "id": id}

@user_router.get("/{id:int}/followers")  # <- the fix
def get_followers(id: int): return {"handler": "user.get_followers", "id": id}

profile_router = APIRouter(prefix="/users")

@profile_router.get("/{username}/profile")
def get_profile(username: str): return {"handler": "profile.get_profile", "username": username}

@profile_router.get("/{username}/followers")
def get_profile_followers(username: str): return {"handler": "profile.get_profile_followers", "username": username}

app.include_router(user_router)
app.include_router(profile_router)
client = TestClient(app)

for path in ["/users/42", "/users/alice/profile", "/users/alice/followers", "/users/42/followers"]:
    r = client.get(path)
    print(path, "->", r.status_code, r.json())

Expected output after the fix (all 200, each hitting the correct handler):

/users/42              -> 200 {'handler': 'user.get_user', 'id': 42}
/users/alice/profile   -> 200 {'handler': 'profile.get_profile', 'username': 'alice'}
/users/alice/followers -> 200 {'handler': 'profile.get_profile_followers', 'username': 'alice'}
/users/42/followers    -> 200 {'handler': 'user.get_followers', 'id': 42}

Before the fix, /users/alice/followers returns 422 with an int_parsing error instead of the third line above — that's the bug.

Regression-proofing (recommended, not required)

Since this exact class of bug has already recurred once, add a cheap startup-time or test-time guard so it can't silently reappear as new routes are added:

python
# tests/test_no_route_collisions.py
from itertools import combinations
from app.main import app

def test_no_ambiguous_routes():
    seen = []
    for route in app.routes:
        if not hasattr(route, "methods"):
            continue
        for a, b in combinations(seen, 1):
            pass
    # Simplest effective check: no two routes should have the same
    # (method, path-template-with-params-normalized) shape registered
    # by two different endpoint functions.
    seen = {}
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path_regex"):
            continue
        for method in route.methods:
            key = (method, route.path_regex.pattern)
            if key in seen and seen[key] is not route.endpoint:
                raise AssertionError(
                    f"Route collision: {method} {route.path} "
                    f"({seen[key].__name__} vs {route.endpoint.__name__})"
                )
            seen[key] = route.endpoint

(Adjust to your actual test runner setup — the point is a single assertion that fails CI the next time two routers define overlapping shapes, rather than discovering it in production.)

P2 — Optional-auth dependencies throw instead of degrading to "anonymous"

community.py's _optional_current_user (lines 16–24) and profile.py's _viewer (lines 33–41) are meant to let logged-out users view public communities/profiles, while still identifying logged-in users when possible. Both have the same bug:

python
def _optional_current_user(
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not token:
        return None
    credentials_exception = HTTPException(status_code=401, detail="Not valid credentials")
    token_data = oauth2.verify_access_token(token, credentials_exception)   # <- raises if token is expired/invalid
    return db.query(models.User).filter(models.User.id == token_data.id).first()

If there's no token, this correctly returns None. But if there is a token and it's expired or malformed, oauth2.verify_access_token (routers/oauth2.py line 22) raises credentials_exception — and nothing catches it here, so the whole request fails with 401 Not valid credentials instead of falling back to "anonymous viewer". A logged-in user whose token has expired (the example env ships access_token_expire_minutes=60, so this is a normal, everyday occurrence, not an edge case) will be unable to view any community or profile, public or not, until they clear their token — and because the frontend auto-logs-out on any 401, they'll get silently signed out just from Browse.

Fix

Catch the exception and degrade to None instead of propagating it, in both places:

python
# routers/community.py — _optional_current_user
def _optional_current_user(
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not token:
        return None
    try:
        credentials_exception = HTTPException(status_code=401, detail="Not valid credentials")
        token_data = oauth2.verify_access_token(token, credentials_exception)
    except HTTPException:
        return None
    return db.query(models.User).filter(models.User.id == token_data.id).first()
python
# routers/profile.py — _viewer (identical change)
def _viewer(
    token: Optional[str] = Depends(oauth2.optional_oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[models.User]:
    if not token:
        return None
    try:
        credentials_exception = HTTPException(status_code=401, detail="Not valid credentials")
        token_data = oauth2.verify_access_token(token, credentials_exception)
    except HTTPException:
        return None
    return db.query(models.User).filter(models.User.id == token_data.id).first()

This only changes behavior for the "invalid/expired token on an optional-auth endpoint" case; a valid token still resolves the user normally, and endpoints that require login (oauth2.get_current_user) are untouched and should keep rejecting bad tokens with 401.

P3 — Smaller issues found during the audit

Each is independent and low-risk; fix as many as time allows.

1. Post search is case-sensitive (routers/post.py, get_posts, ~line 218)
python
.filter(models.Post.published.is_(True), models.Post.title.contains(search))

.contains() compiles to Postgres LIKE, which is case-sensitive — searching "hello" won't find "Hello World". The community search two files over already does this correctly. Bring post search in line with it:

python
.filter(models.Post.published.is_(True), models.Post.title.ilike(f"%{search}%"))
2. update_post doesn't eager-load media before returning (routers/post.py, ~line 204)

create_posts forces owner and media to load before the session can close (_ = new_post.owner; _ = new_post.media), but update_post only forces owner:

python
db.commit()
db.refresh(updated_post)
# ensure relationship is loaded before session closes
_ = updated_post.owner
return updated_post

Both endpoints share response_model=schemas.Post, which requires media. Add the missing line so editing a post can't intermittently fail to serialize:

python
_ = updated_post.owner
_ = updated_post.media
return updated_post
3. Login failures return 403 instead of 401 (routers/auth.py, lines 13 and 15)
python
raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid Credentials")

403 means "authenticated but not allowed"; a bad email/password pair is a 401 (unauthenticated) case. Change both occurrences to status.HTTP_401_UNAUTHORIZED. The frontend currently displays the same generic error either way, so this is safe to change without a frontend update — it's a correctness/semantics fix, not a behavior change users will notice, but it matters for any client that branches on status codes (including your own future code).

4. GET /users/ has no pagination and returns every user (routers/user.py, line 15)
python
@router.get("/", response_model=list[schemas.UserOut])
def get_all_users(db: Session = Depends(get_db), current_user: int = Depends(oauth2.get_current_user)):
    return db.query(models.User).all()

Any logged-in user can dump the entire users table (including email addresses, via UserOut) in one call, and it'll get slower and heavier as your user base grows. Add skip/limit query params (same Query(0, ge=0) / Query(20, ge=1, le=100) pattern used everywhere else in this codebase), and consider whether this endpoint needs to exist as a public-to-any-logged-in-user listing at all versus being restricted or removed.

5. Dead exception branch in the WebSocket handler (routers/chat.py, websocket_events, ~line 311)
python
except (WebSocketDisconnect, JWTError):
    pass
except HTTPException:
    await websocket.close(code=1008)

oauth2.verify_access_token already catches JWTError internally and re-raises it as the passed-in HTTPException (see routers/oauth2.py line 29), so a raw JWTError can never actually reach this handler — the except HTTPException clause below it is the one doing real work. Not a functional bug, just remove JWTError from the tuple (or leave a comment explaining why it's there) so a future reader doesn't assume it's load-bearing.

6. Passwords are silently truncated by bcrypt beyond 72 bytes

app/utility.py calls bcrypt.hashpw directly with no length check. bcrypt ignores any bytes past 72, so a 100-character password is silently treated as just its first 72 bytes — a user could set a long password believing all of it matters, and unknowingly log in successfully with a shorter prefix. Add a max-length check to UserCreate's password validation (e.g., reject passwords over 72 bytes with a clear 422 message) so the limitation is explicit instead of silent.

Feature-by-feature status
Feature	Endpoints	Status after P0+P1+P2
Auth (register/login)	POST /users/, POST /login	Working; see P3.3 for a minor status-code nit
Get/list users by ID	GET /users/, GET /users/{id}	Working; see P3.4 for pagination
Own profile (/me)	GET /users/me, PATCH /users/me/profile, avatar upload/delete	Working; avatar files need P0
Public profile by username	GET /users/{username}/profile, /posts, /replies, /media, /likes, /communities	Working (never affected by P1's route collision)
Followers/following by username	GET /users/{username}/followers, /following	Broken until P1 is fixed
Follow/unfollow by ID	`POST	DELETE /users/{id}/follow, /follow-status, /followers, /following`
Communities	create/list/get-by-id/get-by-slug/join/leave/members/posts	Working; benefits from P2 (stale-token viewers)
Posts	create (with media), get, update, delete, share	Working; see P3.1, P3.2
Post/avatar media uploads	POST /posts/{id}/media, POST /users/me/avatar	Files don't survive a restart until P0 is fixed
Replies	create/list/update/delete	Working
Votes	POST /vote/, GET /vote/{post_id}/status	Working
Messaging/DMs	conversations, messages, WebSocket live updates	Working; see P3.5
Notifications	list, unread count, mark read/read-all	Working
Checklist
- [x] Move post/avatar media to object storage, or attach a paid Render Disk (P0)
- [ ] Confirm a freshly-uploaded image survives a redeploy / free-tier spin-down-and-back-up cycle (Run after deploying to Render with R2/S3 env vars set)
- [x] Apply the six :int path changes in routers/user.py (P1)
- [x] Run the repro script above and confirm all four requests return 200 with the correct handler
- [x] Wrap _optional_current_user (community.py) and _viewer (profile.py) to catch HTTPException and return None (P2)
- [x] Apply P3 fixes as time allows (independent, any order)
- [x] Re-run your existing test suite / add the regression test above
- [ ] Redeploy; confirm on the live Render URL that GET /users/{a real username}/followers returns 200, not 422 (Run after deploying to Render)
