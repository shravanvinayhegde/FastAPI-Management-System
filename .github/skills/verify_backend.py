"""Before/after verification for the VoteFlow backend. Run on a freshly migrated Postgres DB."""
import io, os, sys, time, uuid, subprocess, json, tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); os.chdir(ROOT)
from fastapi.testclient import TestClient
from PIL import Image
import sqlalchemy as sa
from app.main import app
from app.database import engine

c = TestClient(app, raise_server_exceptions=False)
S = uuid.uuid4().hex[:6]
results = []


def check(name, fn):
    try:
        ok, detail = fn()
    except Exception as e:  # noqa
        ok, detail = False, f"EXC {type(e).__name__}: {str(e)[:120]}"
    results.append((name, ok, detail))
    print(("PASS" if ok else "FAIL"), f"{name:34s}|", detail, flush=True)


def reg(email, username=None, pw="password123"):
    p = {"email": email, "password": pw}
    if username:
        p["username"] = username
    return c.post("/users/", json=p)


def hdr(email, pw="password123"):
    r = c.post("/login", data={"username": email, "password": pw})
    return {"Authorization": "Bearer " + r.json()["access_token"]} if r.status_code == 200 else None


def img_png(size=(10, 10), mode="RGB"):
    b = io.BytesIO(); Image.new(mode, size, "red" if mode == "RGB" else 0).save(b, "PNG"); b.seek(0); return b


names = {}
ids = {}
heads = {}
for n in ("alice", "bob", "carol", "dave"):
    email, uname = f"{n}{S}@example.com", f"{n}{S}"
    reg(email, uname)
    heads[n] = hdr(email)
    names[n] = uname
    ids[n] = c.get("/users/me", headers=heads[n]).json()["id"]
ha, hb, hc, hd = (heads[k] for k in ("alice", "bob", "carol", "dave"))

# ---------- 1. follower direction ----------
def f_follow():
    c.post(f"/users/{ids['dave']}/follow", headers=hc)
    nm = lambda r: [u["username"] for u in r.json()] if r.status_code == 200 else r.status_code
    a = nm(c.get(f"/users/{names['dave']}/followers"))
    b = nm(c.get(f"/users/{names['carol']}/following"))
    d = nm(c.get(f"/users/{names['dave']}/following"))
    e = nm(c.get(f"/users/{names['carol']}/followers"))
    ok = a == [names["carol"]] and b == [names["dave"]] and d == [] and e == []
    return ok, f"dave.followers={a} carol.following={b} dave.following={d} carol.followers={e}"
check("01 follower/following direction", f_follow)

# ---------- 2. existing conversation ----------
def f_convo():
    r1 = c.post("/conversations", json={"user_id": ids["alice"]}, headers=hb)
    r2 = c.post("/conversations", json={"user_id": ids["alice"]}, headers=hb)
    ok = r1.status_code in (200, 201) and r2.status_code in (200, 201) and r2.json().get("id") == r1.json().get("id")
    return ok, f"first={r1.status_code} second={r2.status_code}"
check("02 create existing conversation", f_convo)

# ---------- 3/4. email leaks ----------
def f_leak_list():
    r = c.get("/users/?limit=100", headers=hb)
    leaked = [u for u in r.json() if "email" in u]
    r2 = c.get(f"/users/{ids['alice']}", headers=hb)
    return (not leaked and "email" not in r2.json()), f"/users/ leaked {len(leaked)} emails; /users/{{id}} has email={'email' in r2.json()}"
check("03 emails not in /users endpoints", f_leak_list)

cid = c.post("/communities/", json={"name": f"Zincs{S}", "description": "d"}, headers=ha).json()["id"]
cslug = c.get(f"/communities/{cid}").json()["slug"]

def f_members():
    r = c.get(f"/communities/{cid}/members")
    r2 = c.get(f"/communities/{cid}/members", headers=hb)
    body_ok = r2.status_code == 200 and all("email" not in u for u in r2.json())
    return (r.status_code in (401, 403) and body_ok), f"anon={r.status_code} authed_has_email={not body_ok}"
check("04 members needs auth, no emails", f_members)

# ---------- 5. CORS on 500 ----------
def f_cors():
    def boom():
        raise RuntimeError("boom")
    app.add_api_route("/__boom", boom, methods=["GET"])
    r = c.get("/__boom", headers={"Origin": "http://localhost:3000"})
    acao = r.headers.get("access-control-allow-origin")
    return (r.status_code == 500 and acao == "http://localhost:3000"), f"status={r.status_code} ACAO={acao} body={r.text[:40]!r}"
check("05 CORS header on 500", f_cors)

# ---------- 6. pagination validation ----------
def f_limits():
    codes = [c.get(u).status_code for u in ("/posts/?limit=-1", "/posts/?skip=-5", "/posts/?limit=100000")]
    return codes == [422, 422, 422], f"limit=-1,skip=-5,limit=100000 -> {codes}"
check("06 posts pagination validated", f_limits)

# ---------- 7. ordering after an UPDATE ----------
def f_order():
    pids = []
    for i in range(3):
        pids.append(c.post("/posts/", data={"title": f"ord{i}{S}", "content": "x"}, headers=ha).json()["id"])
    c.put(f"/posts/{pids[0]}", json={"title": f"ord0-edited{S}", "content": "x"}, headers=ha)
    got = [p["Post"]["id"] for p in c.get("/posts/?limit=10").json()]
    return got == sorted(got, reverse=True), f"ids returned={got[:6]}"
check("07 feed newest-first after edit", f_order)

# ---------- 8/9/10. drafts ----------
draft_id = c.post("/posts/", data={"title": f"draft{S}", "content": "d", "published": "false"}, headers=ha).json()["id"]
def f_put_draft():
    c.put(f"/posts/{draft_id}", json={"title": f"draft2{S}", "content": "d2"}, headers=ha)
    pub = any(p["Post"]["id"] == draft_id for p in c.get("/posts/?limit=100").json())
    return (not pub), f"draft publicly listed after PUT without 'published' = {pub}"
check("08 PUT keeps draft unpublished", f_put_draft)

def f_owner_draft():
    # make sure it is a draft again
    c.put(f"/posts/{draft_id}", json={"title": "t", "content": "c", "published": False}, headers=ha)
    own = c.get(f"/posts/{draft_id}", headers=ha).status_code
    anon = c.get(f"/posts/{draft_id}").status_code
    return (own == 200 and anon == 404), f"owner={own} anon={anon}"
check("09 owner can open own draft", f_owner_draft)

def f_reply_draft():
    r = c.post(f"/posts/{draft_id}/replies", json={"content": "hi"}, headers=hb)
    return r.status_code == 404, f"reply on draft -> {r.status_code}"
check("10 no replies on someone's draft", f_reply_draft)

# ---------- 11-14 registration ----------
check("11 invalid email rejected", lambda: (lambda r: (r.status_code == 422, f"'notanemail' -> {r.status_code}"))(reg(f"notanemail{S}")))
check("12 case-variant email -> 409", lambda: (lambda r: (r.status_code == 409, f"Alice-case duplicate -> {r.status_code}"))(reg(f"ALICE{S}@example.com")))
check("13 login is case-insensitive", lambda: (lambda r: (r.status_code == 200, f"UPPERCASE email login -> {r.status_code}"))(
    c.post("/login", data={"username": f"ALICE{S}@EXAMPLE.COM", "password": "password123"})))
check("14 short password rejected", lambda: (lambda r: (r.status_code == 422, f"'abc' -> {r.status_code}"))(reg(f"short{S}@example.com", pw="abc")))

# ---------- 15. video filename ----------
def f_video():
    vid = io.BytesIO(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    r = c.post("/posts/", data={"title": f"v{S}", "content": "v"}, files={"video": ("clip.mp4", vid, "video/mp4")}, headers=ha)
    url = r.json()["media"][0]["url"]
    return (".." not in url), url
check("15 video filename single dot", f_video)

# ---------- 16/17 communities ----------
def f_marathi():
    r = c.post("/communities/", json={"name": "मराठी", "description": ""}, headers=ha)
    if r.status_code == 409:  # re-run safety
        return True, "already exists"
    if r.status_code != 201:
        return False, f"create -> {r.status_code} {r.text[:60]}"
    slug = r.json()["slug"]
    g = c.get("/communities/by-slug/" + quote(slug))
    return g.status_code == 200, f"create=201 slug={slug!r} by-slug={g.status_code}"
check("16 non-ASCII community name", f_marathi)
check("17 unknown slug on id route -> 404", lambda: (lambda r: (r.status_code == 404, f"/communities/{cslug} -> {r.status_code}"))(c.get(f"/communities/{cslug}")))

# ---------- 18. websocket holds a pooled DB connection? ----------
def f_ws():
    token = ha["Authorization"].split()[1]
    with c.websocket_connect(f"/ws/events?token={token}") as ws:
        time.sleep(0.6)
        held = engine.pool.checkedout()
    return held == 0, f"pooled connections checked out while 1 idle socket open = {held}"
check("18 websocket releases DB session", f_ws)

# ---------- 19. updated_at ----------
def f_updated():
    time.sleep(1.1)
    c.patch("/users/me/profile", json={"bio": "hello"}, headers=hc)
    with engine.connect() as cn:
        cr, up = cn.execute(sa.text("select created_at, updated_at from users where id=:i"), {"i": ids["carol"]}).one()
    return up > cr, f"created_at==updated_at: {cr == up}"
check("19 users.updated_at changes", f_updated)

# ---------- 20. decompression bomb ----------
def f_bomb():
    buf = img_png((14000, 14000), "1")
    r = c.post("/users/me/avatar", files={"file": ("a.png", buf, "image/png")}, headers=ha)
    return r.status_code == 422, f"14000x14000 PNG avatar -> {r.status_code}"
check("20 decompression bomb -> 422", f_bomb)

# ---------- 21. message notifications cleared on read ----------
def f_notif():
    conv = c.post("/conversations", json={"user_id": ids["alice"]}, headers=hb).json()["id"]
    c.post(f"/conversations/{conv}/messages", json={"content": "hey"}, headers=hb)
    before = c.get("/notifications/unread-count", headers=ha).json()["count"]
    c.post(f"/conversations/{conv}/read", headers=ha)
    after = c.get("/notifications/unread-count", headers=ha).json()["count"]
    return after < before, f"unread before={before} after={after}"
check("21 reading a chat clears its alerts", f_notif)

# ---------- 22/23/24 feed features ----------
def f_top():
    p1 = c.post("/posts/", data={"title": f"low{S}", "content": "x"}, headers=ha).json()["id"]
    p2 = c.post("/posts/", data={"title": f"hi{S}", "content": "x"}, headers=ha).json()["id"]
    c.post("/posts/", data={"title": f"newest{S}", "content": "x"}, headers=ha)
    for h in (hb, hc):
        c.post("/vote/", json={"post_id": p1, "dir": 1}, headers=h)
    for h in (hb, hc, hd):
        c.post("/vote/", json={"post_id": p2, "dir": 1}, headers=h)
    got = [p["Post"]["id"] for p in c.get("/posts/?sort=top&limit=5").json()]
    return got[:2] == [p2, p1], f"sort=top -> {got[:3]} (expected [{p2}, {p1}, ...])"
check("22 GET /posts/?sort=top works", f_top)

def f_voted():
    items = c.get("/posts/?limit=50", headers=hb).json()
    flags = {p["Post"]["id"]: p.get("voted") for p in items}
    voted_ids = [i for i, v in flags.items() if v]
    return len(voted_ids) >= 2, f"posts flagged voted=true for bob: {voted_ids}"
check("23 list exposes viewer's vote state", f_voted)

def f_like():
    c.post("/posts/", data={"title": f"100% sure {S}", "content": "x"}, headers=ha)
    got = [p["Post"]["title"] for p in c.get("/posts/?search=%25&limit=50").json()]
    return (len(got) == 1 and "%" in got[0]), f"search '%' returned {len(got)} posts"
check("24 search escapes LIKE wildcards", f_like)

# ---------- 25. N+1 ----------
def f_nplus1():
    counter = {"n": 0}
    def cnt(*a, **k):
        counter["n"] += 1
    sa.event.listen(engine, "before_cursor_execute", cnt)
    r = c.get("/posts/?limit=10", headers=hb)
    sa.event.remove(engine, "before_cursor_execute", cnt)
    return counter["n"] <= 6, f"{counter['n']} SQL statements for a {len(r.json())}-post page"
check("25 feed avoids N+1 queries", f_nplus1)

# ---------- 26/27. production guards (subprocess) ----------
def run_env(extra):
    env = dict(os.environ); env.update(extra)
    p = subprocess.run([sys.executable, "-c", "import app.database"], cwd=str(ROOT), env=env, capture_output=True, text=True)
    return p.returncode, (p.stderr.strip().splitlines() or [""])[-1][:90]
def f_secret():
    rc, err = run_env({"RENDER": "true", "secret_key": "CHANGE_ME"})
    return rc != 0, f"exit={rc} {err}"
check("26 refuse default JWT secret on Render", f_secret)
def f_dbfallback():
    rc, err = run_env({"RENDER": "true", "secret_key": "x" * 40, "database_hostname": "", "database_url": ""})
    return rc != 0, f"exit={rc} {err}"
check("27 no silent SQLite fallback on Render", f_dbfallback)

p = sum(1 for _, ok, _ in results if ok)
print(f"\nSUMMARY: {p}/{len(results)} checks pass")
out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "backend_verification_results.json"
json.dump([{"name": n, "pass": ok, "detail": d} for n, ok, d in results], open(out_path, "w", encoding="utf-8"), indent=1)
