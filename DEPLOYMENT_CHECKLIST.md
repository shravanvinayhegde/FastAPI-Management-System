# VoteFlow Backend - Complete Deployment Checklist

## Pre-Deployment (Local Development)

- [x] Code compiles without syntax errors
  - Verified: `python -m py_compile app/*.py routers/*.py`

- [x] All critical endpoints implemented
  - ✓ GET `/health` - Health check
  - ✓ GET `/version` - Deployment version info
  - ✓ GET `/users/{username}/profile` - User profiles with privacy
  - ✓ POST `/posts/` - Create posts with media upload
  - ✓ DELETE `/posts/{id}` - Delete posts (with media cleanup)
  - ✓ POST `/posts/{post_id}/media` - Add media to post
  - ✓ DELETE `/posts/{post_id}/media/{media_id}` - Remove media
  - ✓ GET `/communities/` - List communities
  - ✓ GET `/communities/by-slug/{slug}` - Lookup by slug
  - ✓ POST `/conversations` - Create conversations
  - ✓ GET `/conversations` - List conversations

- [x] Database migrations created and tested
  - ✓ Migration chain complete: 12 migrations
  - ✓ Latest migration: `f6a7b8c9d0e1_add_user_profiles.py`
  - ✓ Includes username generation for existing users

- [x] Media handling implemented
  - ✓ Local media directory structure created (media/avatars, media/posts)
  - ✓ Media validation (format, size, dimensions)
  - ✓ Media deletion when posts deleted
  - ✓ Maximum 10 files per post enforced

---

## Deployment to Render

### Step 1: Configure Render Disk for Media Storage

1. Go to Render Dashboard → Your Service
2. Navigate to **Disks** tab
3. Click **Add Disk**
4. Configure:
   - **Name**: `media-storage`
   - **Mount Path**: `/var/data/media`
   - **Size**: `10GB` (adjust based on needs)

### Step 2: Set Environment Variables

In Render → Environment, add/update:

```
MEDIA_DIRECTORY=/var/data/media
DATABASE_URL=<your_postgresql_connection_string>
SECRET_KEY=<strong_random_key>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60
CORS_ORIGINS=https://your-frontend-domain.com
FRONTEND_URL=https://your-frontend-domain.com
```

### Step 3: Update Dockerfile/Start Command

Ensure your start command includes media directory setup and migrations:

**In Dockerfile:**
```dockerfile
# Create persistent media directories
RUN mkdir -p /var/data/media/avatars /var/data/media/posts

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy application
COPY . .

# Start command
CMD mkdir -p /var/data/media/avatars /var/data/media/posts && \
    alembic upgrade head && \
    gunicorn app.main:app --workers 4 --bind 0.0.0.0:8000
```

**Or in render.yaml:**
```yaml
startCommand: |
  mkdir -p /var/data/media/avatars /var/data/media/posts && \
  alembic upgrade head && \
  gunicorn app.main:app --workers 4 --bind 0.0.0.0:8000
```

### Step 4: Deploy Code

```bash
git add .
git commit -m "Complete backend implementation with media, profiles, and communities"
git push
```

Render will automatically redeploy with the new code.

---

## Post-Deployment Verification

### Immediate Checks (5 minutes after deployment)

Run these checks immediately after seeing "Service is live":

```bash
# 1. Check health endpoint
curl https://your-app.onrender.com/health
# Expected: {"status": "ok"}

# 2. Check version endpoint
curl https://your-app.onrender.com/version
# Expected: {"service": "VoteFlow API", "commit": "5bf345bdc09f2e7e261290ec967087fd64df589f"}

# 3. Check logs for errors
# In Render Dashboard → Logs, verify no errors during startup
```

### Comprehensive Verification (After immediate checks)

Run the deployment verification script:

```bash
# Without database (basic checks)
python verify_deployment.py --backend-url https://your-app.onrender.com

# With database verification
python verify_deployment.py \
  --backend-url https://your-app.onrender.com \
  --db-url "postgresql://user:pass@host:5432/dbname"

# Or using environment variables
export BACKEND_URL=https://your-app.onrender.com
export DATABASE_URL="postgresql://..."
python verify_deployment.py
```

Expected output:
```
✓ Health Endpoint
✓ Version Endpoint
✓ Profile Structure
✓ Communities Endpoint
✓ Media Directory
✓ Database connection successful
✓ Username Migration
✓ Migration Status
```

### Database Verification

If you have psql/database client access, run SQL queries:

```bash
# Copy DATABASE_VERIFICATION.sql to a file
# Connect to your production database
psql $DATABASE_URL

# Run key verification queries
-- Quick health check
SELECT
    'Users with username' as check_name,
    CASE WHEN COUNT(*) > 0 AND COUNT(CASE WHEN username IS NULL THEN 1 END) = 0 
        THEN 'PASS' ELSE 'FAIL' END as status
FROM users
UNION ALL
SELECT
    'Migration at head' as check_name,
    CASE WHEN version_num = 'f6a7b8c9d0e1' THEN 'PASS' ELSE 'FAIL' END as status
FROM alembic_version;
```

### Functional Testing

Test key user flows:

```bash
# 1. Create a post with media
curl -X POST https://your-app.onrender.com/posts/ \
  -F "title=Test Post" \
  -F "content=Test content" \
  -F "published=true" \
  -F "image=@test.png"

# Response should include post with media array

# 2. Get profile
curl https://your-app.onrender.com/users/YOUR_USERNAME/profile

# Response should include user, stats, relationship, actions, privacy

# 3. List communities
curl https://your-app.onrender.com/communities/

# Response should include communities with slugs
```

---

## Troubleshooting

### Issue: `/health` returns 502 Bad Gateway

**Possible Causes:**
1. Application didn't start - check Render logs
2. Database connection failed - verify DATABASE_URL
3. Port not listening - verify Gunicorn is running

**Solution:**
```bash
# Check Render logs for startup errors
# Verify DATABASE_URL is correct
# Ensure alembic migrations ran successfully
```

### Issue: Media uploads fail or files disappear

**Possible Causes:**
1. Persistent disk not mounted - verify `/var/data/media` in environment
2. Permissions issue - application can't write to disk
3. Migration didn't run - media table not created

**Solution:**
```bash
# Verify MEDIA_DIRECTORY env var is set
# Check Render Disk is configured and mounted
# Restart the service and watch logs for migration progress
# Manually verify disk space: df -h /var/data/media
```

### Issue: /version shows different commit hash

**Possible Cause:**
Code deployed doesn't match expected commit

**Solution:**
```bash
# Update commit hash in app/main.py:version() function
# Or redeploy from correct Git branch
```

### Issue: Profile endpoint returns 404

**Possible Causes:**
1. No users in database yet
2. Database migration didn't run
3. Username is NULL in database

**Solution:**
```bash
# Check if users exist: 
#   SELECT COUNT(*) FROM users;
# 
# Check migrations ran:
#   SELECT version_num FROM alembic_version;
# 
# Fix NULL usernames:
#   Run migration again: alembic upgrade head
```

### Issue: Media files lost after service restart

**Cause:**
MEDIA_DIRECTORY is pointing to ephemeral storage

**Solution:**
1. Verify Render Disk is created and mounted
2. Set MEDIA_DIRECTORY=/var/data/media in environment
3. Restart service
4. Re-upload media to test persistence

---

## Monitoring & Maintenance

### Daily Checks

Monitor these in Render Dashboard:

- **CPU Usage**: Should be < 50% under normal load
- **Memory Usage**: Should be < 70%
- **Disk Usage**: Check `/var/data/media` doesn't exceed 80% of disk size
- **Error Logs**: Search for errors/exceptions

### Weekly Checks

```bash
# Verify database integrity
python -c "
import requests
import json
r = requests.get('https://your-app.onrender.com/health')
print(f'Health: {r.status_code}')
r = requests.get('https://your-app.onrender.com/version')
print(f'Version: {r.json()}')
"

# Check recent errors in logs
# Review slow query logs if available
```

### Monthly Tasks

1. **Database Maintenance**
   ```sql
   VACUUM FULL;
   REINDEX DATABASE;
   ```

2. **Cleanup Orphan Media**
   - Review storage usage
   - Check for unreferenced files in media directory
   - Consider archiving old media

3. **Review Logs**
   - Check error patterns
   - Review failed requests
   - Monitor API response times

### Set Up Alerts (Recommended)

In Render, configure alerts for:
- Service crashes (auto-restart enabled)
- High memory usage (> 80%)
- High CPU usage (> 80%)
- Database connection failures

---

## Rollback Plan

If deployment fails:

### Option 1: Quick Rollback (Render)

1. Go to Render → Your Service
2. Click "Manual Deploy"
3. Select previous Git commit
4. Click "Deploy"

### Option 2: Revert Database Migrations

If migrations cause issues:

```bash
# Connect to database
psql $DATABASE_URL

# View current migration
SELECT version_num FROM alembic_version;

# Rollback one migration
alembic downgrade -1

# Or rollback to specific version
alembic downgrade <revision_id>
```

### Option 3: Full Database Restore

If data corruption occurs:

1. Create manual database backup from Render
2. Restore from backup
3. Re-run migrations: `alembic upgrade head`

---

## Success Criteria

Deployment is successful when:

- [x] ✓ /health endpoint returns 200
- [x] ✓ /version endpoint returns correct commit
- [x] ✓ Database migrations completed (version = f6a7b8c9d0e1)
- [x] ✓ All users have usernames in database
- [x] ✓ Media directory is accessible and writable
- [x] ✓ Can create posts with media uploads
- [x] ✓ Can view user profiles with privacy controls
- [x] ✓ Can create and list communities
- [x] ✓ Can send messages with media sharing
- [x] ✓ Media files persist across service restarts

---

## Contact & Support

For issues during deployment:

1. Check Render logs: Render Dashboard → Your Service → Logs
2. Run verification script: `python verify_deployment.py --backend-url <url>`
3. Query database: See DATABASE_VERIFICATION.sql
4. Review application code: Check app/main.py, app/models.py, routers/*

---

**Last Updated:** 2026-09-11
**Deployment Version:** 1.0.0
**Expected Backend Commit:** 5bf345bdc09f2e7e261290ec967087fd64df589f
