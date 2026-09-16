from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient


def test_user_profile_followers_routes_do_not_collide():
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

    assert client.get("/users/42").json() == {"handler": "user.get_user", "id": 42}
    assert client.get("/users/alice/profile").json() == {
        "handler": "profile.get_profile",
        "username": "alice",
    }
    assert client.get("/users/alice/followers").json() == {
        "handler": "profile.get_profile_followers",
        "username": "alice",
    }
    assert client.get("/users/42/followers").json() == {"handler": "user.get_followers", "id": 42}
