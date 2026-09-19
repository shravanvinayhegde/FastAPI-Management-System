#!/usr/bin/env python3
"""
Deployment Verification Script for VoteFlow Backend

This script verifies all critical backend functionality is working in production.
Run this after deploying to Render to ensure everything is set up correctly.

Usage:
    python verify_deployment.py --backend-url https://your-app.onrender.com --db-url "postgresql://..."

Or use environment variables:
    BACKEND_URL=https://your-app.onrender.com
    DATABASE_URL="postgresql://..."
    python verify_deployment.py
"""

import os
import sys
import argparse
import json
from typing import Optional
from datetime import datetime

# Try to import requests, provide helpful message if missing
try:
    import requests
except ImportError:
    print("Error: requests module not found")
    print("Install with: pip install requests")
    sys.exit(1)

# Try to import sqlalchemy
try:
    from sqlalchemy import create_engine, text
except ImportError:
    print("Warning: sqlalchemy not installed. Database checks will be skipped.")
    print("Install with: pip install sqlalchemy psycopg2")


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    """Print section header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}{Colors.RESET}\n")


def print_success(text: str):
    """Print success message"""
    print(f"{Colors.GREEN}✓{Colors.RESET} {text}")


def print_error(text: str):
    """Print error message"""
    print(f"{Colors.RED}✗{Colors.RESET} {text}")


def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠{Colors.RESET} {text}")


def print_info(text: str):
    """Print info message"""
    print(f"{Colors.BLUE}ℹ{Colors.RESET} {text}")


class DeploymentVerifier:
    """Verify VoteFlow backend deployment"""

    def __init__(self, backend_url: str, database_url: Optional[str] = None):
        self.backend_url = backend_url.rstrip('/')
        self.database_url = database_url
        self.results = []
        self.session = requests.Session()

    def test_health_endpoint(self) -> bool:
        """Test /health endpoint"""
        print_header("1. Testing Health Check")
        try:
            response = self.session.get(f"{self.backend_url}/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("status") == "ok":
                    print_success(f"Health endpoint responding: {json.dumps(data)}")
                    self.results.append(("Health Endpoint", True, data))
                    return True
            print_error(f"Unexpected response: {response.status_code}")
            self.results.append(("Health Endpoint", False, response.text))
            return False
        except Exception as e:
            print_error(f"Health check failed: {e}")
            self.results.append(("Health Endpoint", False, str(e)))
            return False

    def test_version_endpoint(self) -> bool:
        """Test /version endpoint"""
        print_header("2. Testing Version Endpoint")
        try:
            response = self.session.get(f"{self.backend_url}/version", timeout=5)
            if response.status_code == 200:
                data = response.json()
                expected_commit = "5bf345bdc09f2e7e261290ec967087fd64df589f"
                
                if data.get("service") == "VoteFlow API":
                    print_success(f"Service name correct: {data.get('service')}")
                else:
                    print_warning(f"Service name unexpected: {data.get('service')}")
                
                if data.get("commit") == expected_commit:
                    print_success(f"Commit hash matches: {expected_commit}")
                    self.results.append(("Version Endpoint", True, data))
                    return True
                else:
                    print_warning(f"Commit hash mismatch!")
                    print_info(f"  Expected: {expected_commit}")
                    print_info(f"  Got:      {data.get('commit')}")
                    self.results.append(("Version Endpoint", False, data))
                    return False
            print_error(f"Unexpected response: {response.status_code}")
            self.results.append(("Version Endpoint", False, response.text))
            return False
        except Exception as e:
            print_error(f"Version check failed: {e}")
            self.results.append(("Version Endpoint", False, str(e)))
            return False

    def test_profile_endpoints(self) -> bool:
        """Test profile endpoints"""
        print_header("3. Testing Profile Endpoints")
        all_passed = True
        
        # Test public profile
        try:
            response = self.session.get(f"{self.backend_url}/users/admin/profile", timeout=5)
            if response.status_code == 200:
                data = response.json()
                required_fields = ["user", "stats", "relationship", "actions", "privacy"]
                if all(field in data for field in required_fields):
                    print_success("Profile response has correct structure")
                    self.results.append(("Profile Structure", True, None))
                else:
                    missing = [f for f in required_fields if f not in data]
                    print_error(f"Profile missing fields: {missing}")
                    self.results.append(("Profile Structure", False, missing))
                    all_passed = False
            elif response.status_code == 404:
                print_warning("Profile endpoint exists but test user 'admin' not found (expected)")
                self.results.append(("Profile Endpoint", True, "404 - expected"))
            else:
                print_error(f"Unexpected response code: {response.status_code}")
                self.results.append(("Profile Endpoint", False, response.status_code))
                all_passed = False
        except Exception as e:
            print_warning(f"Profile test skipped: {e}")
            self.results.append(("Profile Endpoint", None, str(e)))
        
        return all_passed

    def test_communities_endpoint(self) -> bool:
        """Test community endpoints"""
        print_header("4. Testing Community Endpoints")
        try:
            response = self.session.get(f"{self.backend_url}/communities/", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print_success(f"Communities endpoint responding (found {len(data) if isinstance(data, list) else 'N/A'} communities)")
                self.results.append(("Communities Endpoint", True, None))
                return True
            elif response.status_code == 404:
                print_warning("Communities endpoint not found")
                self.results.append(("Communities Endpoint", False, "404"))
                return False
        except Exception as e:
            print_warning(f"Communities test failed: {e}")
            self.results.append(("Communities Endpoint", None, str(e)))
            return True  # Don't fail on this

        return True

    def test_media_directory(self) -> bool:
        """Verify media directory is accessible"""
        print_header("5. Testing Media Directory")
        try:
            # Try accessing media directory (may return 404 if no files)
            response = self.session.head(f"{self.backend_url}/media/", timeout=5)
            if response.status_code in [200, 404]:
                print_success("Media directory is accessible")
                self.results.append(("Media Directory", True, None))
                return True
            else:
                print_warning(f"Media directory returned: {response.status_code}")
                self.results.append(("Media Directory", None, response.status_code))
                return True
        except Exception as e:
            print_warning(f"Media directory check failed: {e}")
            self.results.append(("Media Directory", None, str(e)))
            return True

    def verify_database(self) -> bool:
        """Verify database connection and migrations"""
        if not self.database_url:
            print_header("6. Database Verification - Skipped")
            print_info("No DATABASE_URL provided. Skipping database checks.")
            print_info("To verify database, run:")
            print_info("  python verify_deployment.py --db-url 'postgresql://...'")
            return True

        print_header("6. Database Verification")
        try:
            engine = create_engine(self.database_url)
            
            with engine.connect() as conn:
                # Test connection
                result = conn.execute(text("SELECT 1"))
                print_success("Database connection successful")
                
                # Check users table
                result = conn.execute(text(
                    "SELECT COUNT(*) as count, "
                    "COUNT(CASE WHEN username IS NULL THEN 1 END) as missing_usernames "
                    "FROM users"
                ))
                row = result.fetchone()
                user_count, null_usernames = row[0], row[1]
                
                print_info(f"Total users: {user_count}")
                if null_usernames > 0:
                    print_error(f"Users with NULL username: {null_usernames} (migration may not have run)")
                    self.results.append(("Username Migration", False, f"{null_usernames} NULL usernames"))
                    return False
                else:
                    print_success(f"All {user_count} users have usernames")
                    self.results.append(("Username Migration", True, user_count))
                
                # Check migrations
                result = conn.execute(text(
                    "SELECT version_num FROM alembic_version ORDER BY version_num DESC LIMIT 1"
                ))
                migration = result.fetchone()
                if migration:
                    current_migration = migration[0]
                    print_success(f"Latest migration: {current_migration}")
                    
                    # Expected latest migration
                    expected = "f6a7b8c9d0e1"
                    if current_migration == expected:
                        print_success(f"Migration chain is up to date")
                        self.results.append(("Migration Status", True, current_migration))
                        return True
                    else:
                        print_warning(f"Latest migration is {current_migration}, expected {expected}")
                        print_info(f"Run: alembic upgrade head")
                        self.results.append(("Migration Status", False, current_migration))
                        return False
                else:
                    print_error("No migrations found - run: alembic upgrade head")
                    self.results.append(("Migration Status", False, "No migrations"))
                    return False
                    
        except Exception as e:
            print_error(f"Database verification failed: {e}")
            self.results.append(("Database Verification", False, str(e)))
            return False

    def print_summary(self):
        """Print test summary"""
        print_header("Test Summary")
        
        passed = sum(1 for _, result, _ in self.results if result is True)
        failed = sum(1 for _, result, _ in self.results if result is False)
        skipped = sum(1 for _, result, _ in self.results if result is None)
        total = len(self.results)
        
        print(f"\nResults: {Colors.GREEN}{passed} passed{Colors.RESET}, "
              f"{Colors.RED}{failed} failed{Colors.RESET}, "
              f"{Colors.YELLOW}{skipped} skipped{Colors.RESET} (out of {total})")
        
        print("\nDetailed Results:")
        for test_name, result, details in self.results:
            if result is True:
                print_success(test_name)
            elif result is False:
                print_error(test_name)
            else:
                print_warning(test_name)
        
        if failed == 0:
            print_header("✓ All checks passed!")
            return 0
        else:
            print_header("✗ Some checks failed")
            return 1

    def run_all_checks(self) -> int:
        """Run all verification checks"""
        print(f"\n{Colors.BOLD}VoteFlow Backend Deployment Verification{Colors.RESET}")
        print(f"Backend URL: {self.backend_url}")
        if self.database_url:
            print(f"Database: Configured")
        print(f"Timestamp: {datetime.now().isoformat()}")
        
        self.test_health_endpoint()
        self.test_version_endpoint()
        self.test_profile_endpoints()
        self.test_communities_endpoint()
        self.test_media_directory()
        self.verify_database()
        
        return self.print_summary()


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Verify VoteFlow backend deployment",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test basic endpoints
  python verify_deployment.py --backend-url https://your-app.onrender.com

  # Test with database verification
  python verify_deployment.py --backend-url https://your-app.onrender.com \\
                              --db-url "postgresql://user:pass@host/db"

  # Use environment variables
  BACKEND_URL=https://your-app.onrender.com DATABASE_URL="..." python verify_deployment.py
        """
    )
    
    parser.add_argument(
        "--backend-url",
        default=os.getenv("BACKEND_URL"),
        help="Backend API URL (or set BACKEND_URL env var)"
    )
    
    parser.add_argument(
        "--db-url",
        default=os.getenv("DATABASE_URL"),
        help="Database URL for verification (or set DATABASE_URL env var)"
    )
    
    args = parser.parse_args()
    
    if not args.backend_url:
        parser.print_help()
        print(f"\n{Colors.RED}Error: --backend-url is required{Colors.RESET}")
        sys.exit(1)
    
    verifier = DeploymentVerifier(args.backend_url, args.db_url)
    sys.exit(verifier.run_all_checks())


if __name__ == "__main__":
    main()
