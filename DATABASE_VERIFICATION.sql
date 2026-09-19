-- VoteFlow Backend Database Verification Queries
-- Run these in your production database to verify the setup

-- ============================================================================
-- 1. Check all users have usernames (migration verification)
-- ============================================================================

-- List all users with their username status
SELECT 
    id,
    email,
    username,
    display_name,
    CASE 
        WHEN username IS NULL THEN 'MISSING'
        WHEN username = '' THEN 'EMPTY'
        ELSE 'OK'
    END as username_status,
    created_at
FROM users
ORDER BY id;

-- Count users with missing usernames (should be 0)
SELECT 
    COUNT(*) as total_users,
    COUNT(CASE WHEN username IS NULL THEN 1 END) as missing_usernames,
    COUNT(CASE WHEN username = '' THEN 1 END) as empty_usernames
FROM users;

-- ============================================================================
-- 2. Verify specific users can be found (case-insensitive)
-- ============================================================================

-- Test looking up users by username (case-insensitive)
SELECT id, username, email, display_name FROM users 
WHERE LOWER(username) = LOWER('karan_2');

SELECT id, username, email, display_name FROM users 
WHERE LOWER(username) = LOWER('shravanvinayhegde');

-- ============================================================================
-- 3. Check migration status
-- ============================================================================

-- View current migration version
SELECT version_num, installed_on FROM alembic_version;

-- This should show: f6a7b8c9d0e1 (or later)
-- If not, run in your application: alembic upgrade head

-- ============================================================================
-- 4. Verify database schema - Check key tables exist
-- ============================================================================

-- List all tables
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;

-- Verify posts table has media relationship
\d posts;
\d post_media;

-- ============================================================================
-- 5. Check communities are properly set up
-- ============================================================================

-- List all communities (verify slug is unique and populated)
SELECT 
    id,
    name,
    slug,
    CASE 
        WHEN slug IS NULL THEN 'MISSING'
        WHEN slug = '' THEN 'EMPTY'
        ELSE 'OK'
    END as slug_status,
    creator_id,
    created_at
FROM communities
ORDER BY id;

-- Verify slug uniqueness
SELECT slug, COUNT(*) as count
FROM communities
GROUP BY slug
HAVING COUNT(*) > 1;

-- Should return 0 rows (no duplicates)

-- ============================================================================
-- 6. Verify conversations have proper structure
-- ============================================================================

-- Check conversations exist and have proper user relationships
SELECT 
    id,
    user_one_id,
    user_two_id,
    created_at,
    updated_at
FROM conversations
LIMIT 10;

-- Verify ordered pair constraint (user_one_id < user_two_id)
SELECT id, user_one_id, user_two_id
FROM conversations
WHERE user_one_id >= user_two_id;

-- Should return 0 rows (constraint enforced)

-- ============================================================================
-- 7. Check media uploads (if any have been made)
-- ============================================================================

-- Count media by type
SELECT 
    media_type,
    COUNT(*) as count,
    ROUND(SUM(size_bytes) / 1024 / 1024, 2) as total_size_mb
FROM post_media
GROUP BY media_type;

-- List recent media
SELECT 
    id,
    post_id,
    media_type,
    storage_key,
    size_bytes,
    ROUND(size_bytes / 1024 / 1024, 2) as size_mb,
    created_at
FROM post_media
ORDER BY created_at DESC
LIMIT 20;

-- ============================================================================
-- 8. Check profile visibility settings
-- ============================================================================

-- Verify profile_visibility values are valid (public/private)
SELECT 
    profile_visibility,
    COUNT(*) as count
FROM users
GROUP BY profile_visibility;

-- Find any users with invalid visibility values
SELECT id, username, profile_visibility
FROM users
WHERE profile_visibility NOT IN ('public', 'private');

-- Should return 0 rows

-- ============================================================================
-- 9. Database integrity checks
-- ============================================================================

-- Check for orphaned posts (no owner)
SELECT id, title FROM posts WHERE owner_id IS NULL;

-- Check for posts referencing non-existent communities
SELECT p.id, p.community_id FROM posts p 
LEFT JOIN communities c ON p.community_id = c.id 
WHERE p.community_id IS NOT NULL AND c.id IS NULL;

-- Check for messages referencing non-existent conversations
SELECT m.id, m.conversation_id FROM messages m 
LEFT JOIN conversations c ON m.conversation_id = c.id 
WHERE c.id IS NULL;

-- All three queries above should return 0 rows

-- ============================================================================
-- 10. Database size and performance
-- ============================================================================

-- Database size
SELECT pg_size_pretty(pg_database_size(current_database())) as database_size;

-- Table sizes
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Check index health
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan as index_scans
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;

-- ============================================================================
-- Quick Health Check (run this one query)
-- ============================================================================

SELECT
    'Users with username' as check_name,
    CASE WHEN COUNT(*) > 0 AND COUNT(CASE WHEN username IS NULL THEN 1 END) = 0 
        THEN 'PASS' ELSE 'FAIL' END as status,
    COUNT(*) as user_count
FROM users
UNION ALL
SELECT
    'Communities with slug' as check_name,
    CASE WHEN COUNT(*) > 0 AND COUNT(CASE WHEN slug IS NULL THEN 1 END) = 0 
        THEN 'PASS' ELSE 'FAIL' END as status,
    COUNT(*) as community_count
FROM communities
UNION ALL
SELECT
    'Migration at head' as check_name,
    CASE WHEN version_num = 'f6a7b8c9d0e1' THEN 'PASS' ELSE 'FAIL' END as status,
    1 as count
FROM alembic_version
ORDER BY check_name;
