from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app import storage
from routers import auth, chat, community, notification, post, profile, user, vote

app = FastAPI()

if storage.uses_object_storage():
    @app.get("/media/{key:path}")
    def media_redirect(key: str):
        return RedirectResponse(storage.public_url(key))
else:
    settings.media_directory.mkdir(parents=True, exist_ok=True)
    (settings.media_directory / "avatars").mkdir(parents=True, exist_ok=True)
    (settings.media_directory / "posts").mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(settings.media_directory)), name="media")

origins = settings.cors_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=origins != ["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {
        "service": "VoteFlow API",
        "commit": "5bf345bdc09f2e7e261290ec967087fd64df589f",
    }


app.include_router(post.router)
app.include_router(user.router)
app.include_router(auth.router)
app.include_router(vote.router)
app.include_router(community.router)
app.include_router(chat.router)
app.include_router(notification.router)
app.include_router(profile.router)
