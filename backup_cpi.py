#!/usr/bin/env python3

import datetime
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth

# =====================================================
# Configuration (from GitLab CI variables or .env file)
# =====================================================

try:
    from dotenv import load_dotenv
    load_dotenv()  # Load environment variables from .env file
    print("Loaded configuration from .env file")
except ImportError:
    pass  # Continue with environment variables if python-dotenv not available

SAP_HOST = os.environ["SAP_HOST"].rstrip("/")
SAP_USER = os.environ["SAP_USER"]
SAP_PASSWORD = os.environ["SAP_PASSWORD"]

ARTIFACTS_DIR = Path("artifacts")
GIT_BRANCH_PREFIX = "backup-"
GIT_COMMIT_MESSAGE = "Backup SAP Integration Suite artifacts"

# =====================================================
# HTTP Session
# =====================================================

session = requests.Session()
session.auth = HTTPBasicAuth(SAP_USER, SAP_PASSWORD)

session.headers.update(
    {
        "Accept": "application/json"
    }
)

# =====================================================
# Git Configuration
# =====================================================

try:
    import git
except ImportError:
    git = None
    print("Warning: gitpython not installed. Git operations will be skipped.")

# =====================================================
# Helpers
# =====================================================

def get_artifact_path(artifact_id: str, package_id: str, artifact_type: str) -> Path:
    """
    Generate organized path for an artifact based on package and type.
    Falls back to simple structure if package or type is missing.
    """
    if not package_id or not artifact_type:
        return ARTIFACTS_DIR / artifact_id
    
    # Normalize package name for filesystem (replace special chars)
    safe_package = "".join(c if c.isalnum() else "_" for c in package_id)
    safe_type = "".join(c if c.isalnum() else "_" for c in artifact_type.lower()).replace("_", "-")
    
    return ARTIFACTS_DIR / safe_package / safe_type / artifact_id

def clean_directory(directory: Path):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)

def download_artifact(artifact_id, version, max_retries=3):
    """
    Download artifact with retry logic.
    Returns None if all retries fail.
    """
    download_url = (
        f"{SAP_HOST}/api/v1/"
        f"IntegrationDesigntimeArtifacts"
        f"(Id='{artifact_id}',Version='{version}')/$value"
    )

    for attempt in range(max_retries):
        try:
            response = session.get(download_url, timeout=300)
            if response.status_code == 200:
                return response.content
            elif response.status_code == 404:
                print(f"Artifact {artifact_id} not found (attempt {attempt + 1})")
                break
            else:
                print(f"Failed downloading {artifact_id}. HTTP {response.status_code} (attempt {attempt + 1})")
        except Exception as e:
            print(f"Error downloading {artifact_id}: {str(e)} (attempt {attempt + 1})")
    
    return None

# =====================================================
# Read package inventory first
# =====================================================

packages_url = f"{SAP_HOST}/api/v1/IntegrationPackages"
response = session.get(packages_url, timeout=120)
response.raise_for_status()

payload = response.json()
packages = payload["d"]["results"]

print(f"Found {len(packages)} packages")

# =====================================================
# Read artifact inventory for each package
# =====================================================

artifacts = []

for package in packages:
    package_id = package["Id"]
    print(f"Processing package: {package_id}")
    
    artifacts_url = f"{SAP_HOST}/api/v1/IntegrationPackages('{package_id}')/IntegrationDesigntimeArtifacts"
    response = session.get(artifacts_url, timeout=120)
    
    if response.status_code == 200:
        payload = response.json()
        package_artifacts = payload["d"]["results"]
        artifacts.extend(package_artifacts)
        print(f"Found {len(package_artifacts)} artifacts in package {package_id}")
    else:
        print(f"Warning: Failed to get artifacts for package {package_id}. HTTP {response.status_code}")

print(f"Total artifacts found: {len(artifacts)}")

ARTIFACTS_DIR.mkdir(exist_ok=True)

# =====================================================
# Process all artifacts
# =====================================================

for artifact in artifacts:
    artifact_id = artifact["Id"]
    version = artifact["Version"]
    package_id = artifact.get("PackageId", "")
    artifact_type = artifact.get("ArtifactType", "")

    print(f"Exporting {artifact_id} ({version}) - Package: {package_id}, Type: {artifact_type}")

    # Use organized path structure
    target_dir = get_artifact_path(artifact_id, package_id, artifact_type)

    clean_directory(target_dir)

    zip_bytes = download_artifact(artifact_id, version)
    
    if zip_bytes is None:
        print(f"Skipping {artifact_id} - Download failed after retries")
        continue

    with tempfile.NamedTemporaryFile(
        suffix=".zip",
        delete=False
    ) as temp_zip:
        temp_zip.write(zip_bytes)
        zip_file = temp_zip.name

    try:
        with zipfile.ZipFile(zip_file) as zf:
            # Validate zip file before extraction
            if not zf.testzip():
                zf.extractall(target_dir)
            else:
                print(f"Warning: Corrupt zip for {artifact_id}")
                continue

        metadata = {
            "id": artifact_id,
            "version": version,
            "name": artifact.get("Name"),
            "packageId": package_id,
            "type": artifact_type,
            "path": str(target_dir.relative_to(ARTIFACTS_DIR)),
            "timestamp": datetime.datetime.now().isoformat()
        }

        with open(
            target_dir / "artifact-metadata.json",
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                metadata,
                f,
                indent=2,
                ensure_ascii=False
            )

    finally:
        os.remove(zip_file)

print("Export complete")

# =====================================================
# Git Operations (if gitpython is available)
# =====================================================

if git:
    try:
        repo = git.Repo(search_parent_directories=True)
        current_branch = repo.active_branch.name
        
        # Create new branch for this backup
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        new_branch_name = f"{GIT_BRANCH_PREFIX}{timestamp}"
        
        try:
            repo.git.checkout("HEAD", b=new_branch_name)
        except git.exc.GitCommandError:
            # Branch already exists, checkout existing
            repo.git.checkout(new_branch_name)
        
        # Add all changes and commit
        repo.git.add(all=True, path=ARTIFACTS_DIR)
        
        if repo.index.diff("HEAD"):
            commit_message = f"{GIT_COMMIT_MESSAGE} - {timestamp}"
            repo.index.commit(commit_message)
            
            # Push to remote
            try:
                origin = repo.remote(name="origin")
                origin.push(new_branch_name)
                print(f"Pushed changes to branch: {new_branch_name}")
            except Exception as e:
                print(f"Failed to push changes: {str(e)}")
        else:
            print("No changes to commit")
            
    except Exception as e:
        print(f"Git operations failed: {str(e)}")