"""Run: python repro_router_collision.py — confirms P1 routing fix (see .github/update.md)."""
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

app = FastAPI()
user_router = APIRouter(prefix="/users")


@user_router.get("/{id:int}")
def get_user(id: int):
    return {"handler": "user.get_user", "id": id}


@user_router.get("/{id:int}/followers")
def get_followers(id: int):
    return {"handler": "user.get_followers", "id": id}


profile_router = APIRouter(prefix="/users")


@profile_router.get("/{username}/profile")
def get_profile(username: str):
    return {"handler": "profile.get_profile", "username": username}


@profile_router.get("/{username}/followers")
def get_profile_followers(username: str):
    return {"handler": "profile.get_profile_followers", "username": username}


app.include_router(user_router)
app.include_router(profile_router)
client = TestClient(app)

for path in ["/users/42", "/users/alice/profile", "/users/alice/followers", "/users/42/followers"]:
    r = client.get(path)
    print(path, "->", r.status_code, r.json())
