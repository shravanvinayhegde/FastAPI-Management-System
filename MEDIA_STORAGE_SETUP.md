# Media Storage Setup Guide

## Local Development Setup

The media directory structure is already configured locally:

```
media/
├── avatars/     # User profile pictures
└── posts/       # Post media (images/videos)
```

This is configured in `app/config.py`:
```python
media_directory: Path = Path("media")
```

And mounted in `app/main.py`:
```python
app.mount("/media", StaticFiles(directory=str(settings.media_directory)), name="media")
```

## Production Setup (Render)

For production deployment on Render, media must be stored on a persistent disk.

### Step 1: Create Render Disk

1. Go to Render Dashboard
2. Navigate to your Web Service
3. Go to "Disks" tab
4. Click "Add Disk"
5. Configure:
   - **Name**: `media-storage`
   - **Mount Path**: `/var/data/media`
   - **Size**: 10GB (or larger as needed)

### Step 2: Configure Environment Variables

Add to your Render environment variables:

```
MEDIA_DIRECTORY=/var/data/media
```

### Step 3: Verify in Dockerfile

Your Dockerfile should:
1. Create media directories before start
2. Run migrations before Gunicorn

Ensure your start command includes:
```bash
# Create media directories
python -c "from pathlib import Path; Path('/var/data/media/avatars').mkdir(parents=True, exist_ok=True); Path('/var/data/media/posts').mkdir(parents=True, exist_ok=True)"

# Run migrations
alembic upgrade head

# Start server
gunicorn app.main:app --workers 4
```

### Step 4: Update Render Config

If using `render.yaml`:

```yaml
services:
  - type: web
    name: voteflow-api
    env: python
    plan: starter
    buildCommand: pip install -r requirements.txt
    startCommand: |
      python -c "from pathlib import Path; 
      Path('/var/data/media/avatars').mkdir(parents=True, exist_ok=True); 
      Path('/var/data/media/posts').mkdir(parents=True, exist_ok=True)"
      && alembic upgrade head
      && gunicorn app.main:app --workers 4
    disk:
      name: media-storage
      mountPath: /var/data/media
      sizeGB: 10
```

### Step 5: Verify Media Persistence

After deployment:

1. Upload a file to `/posts/` endpoint
2. Take note of the returned media URL
3. Restart the service
4. Verify the file still exists at the URL

## Testing Media Upload Locally

```bash
# Start the server
python -m uvicorn app.main:app --reload

# In another terminal, test media upload
curl -X POST \
  -F "title=Test Post" \
  -F "content=Test content" \
  -F "published=true" \
  -F "image=@test.png" \
  http://localhost:8000/posts/
```

## Troubleshooting

### Issue: Media files disappear after restart
- **Solution**: Verify `MEDIA_DIRECTORY` environment variable is set to persistent disk
- Check Docker logs: `docker logs <container-id>`

### Issue: Permission denied writing to media directory
- **Solution**: Ensure directory is writable by the application user
- In Dockerfile: `RUN chmod 755 /var/data/media`

### Issue: Media URL returns 404
- **Solution**: Verify static files mount is configured correctly
- Check `app.mount("/media", StaticFiles(...))` in `app/main.py`

## Media Retention Policy

- **Avatar files**: Kept until user account deleted
- **Post media**: Deleted when post is deleted
- **Orphan cleanup**: Implement periodic cleanup job to remove unreferenced files

```python
# Optional: Add orphan cleanup in a background task
from app.models import PostMedia

def cleanup_orphan_media(db: Session):
    """Remove media files not referenced in database"""
    existing_files = set(settings.media_directory.glob("posts/*"))
    db_media = db.query(PostMedia.storage_key).all()
    db_storage_keys = {f"posts/{m[0].split('/')[-1]}" for m in db_media}
    
    for file_path in existing_files:
        relative = str(file_path.relative_to(settings.media_directory))
        if relative not in db_storage_keys:
            file_path.unlink()
```

## Security Considerations

1. **File Type Validation**: Enforce in `_store_post_media()` (already implemented)
2. **File Size Limits**: Configured via `max_image_upload_mb` and `max_video_upload_mb`
3. **Access Control**: Media is public via `/media` mount. For private media, return presigned URLs
4. **Disk Quotas**: Monitor disk usage on Render; add alerting at 80% capacity
