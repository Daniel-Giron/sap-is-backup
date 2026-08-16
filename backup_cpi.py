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

ARTIFACT_TYPES: dict[str, dict[str, str]] = {
    "iflow": {"files_path": "iflows", "odata_path": "IntegrationDesigntimeArtifacts"},
    "message_mapping": {
        "files_path": "message_mappings",
        "odata_path": "MessageMappingDesigntimeArtifacts",
    },
    "value_mapping": {
        "files_path": "value_mappings",
        "odata_path": "ValueMappingDesigntimeArtifacts",
    },
    "script_collection": {
        "files_path": "script_collections",
        "odata_path": "ScriptCollectionDesigntimeArtifacts",
    },
    "message_type": {
        "files_path": "message_types",
        "odata_path": "MessageTypeDesigntimeArtifacts",
    },
    "fault_message_type": {
        "files_path": "fault_message_types",
        "odata_path": "FaultMessageTypeDesigntimeArtifacts",
    },
    "data_type": {
        "files_path": "data_types",
        "odata_path": "DataTypeDesigntimeArtifacts",
    },
    "service_interface": {
        "files_path": "service_interfaces",
        "odata_path": "ServiceInterfaceDesigntimeArtifacts",
    },
}

ARTIFACTS_DIR = Path("artifacts")
GIT_BRANCH_PREFIX: str = "backup-"
GIT_COMMIT_MESSAGE: str = "Backup SAP Integration Suite artifacts"

# =====================================================
# HTTP Session
# =====================================================

session = requests.Session()
session.auth = HTTPBasicAuth(SAP_USER, SAP_PASSWORD)

session.headers.update({"Accept": "application/json"})

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
    # Normalize package name for filesystem (replace special chars)
    safe_package = "".join(c if c.isalnum() else "_" for c in package_id)

    return (
        ARTIFACTS_DIR
        / safe_package
        / ARTIFACT_TYPES[artifact_type]["files_path"]
        / artifact_id
    )


def get_subobject_path(artifact_id: str, package_id: str, subobject_type: str) -> Path:

    safe_package = "".join(c if c.isalnum() else "_" for c in package_id)

    return (
        ARTIFACTS_DIR / safe_package / "integration-flow" / artifact_id / subobject_type
    )


def clean_directory(directory: Path):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)


def download_artifact(artifact_id, version, artifact_type, max_retries=3):
    """
    Download artifact with retry logic.
    Returns None if all retries fail.
    """
    download_url = (
        f"{SAP_HOST}/api/v1/"
        f"{ARTIFACT_TYPES[artifact_type]['odata_path']}"
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
                print(
                    f"Failed downloading {artifact_id}. HTTP {response.status_code} (attempt {attempt + 1})"
                )
        except Exception as e:
            print(f"Error downloading {artifact_id}: {str(e)} (attempt {attempt + 1})")

    return None


# =====================================================
# Helper function to download and extract artifacts
# =====================================================


def download_and_extract_artifact(artifact_id, version, target_dir, artifact_type):
    """
    Download artifact with retry logic and extract to target directory.
    Returns True if successful, False otherwise.
    """
    download_url = (
        f"{SAP_HOST}/api/v1/"
        f"{ARTIFACT_TYPES[artifact_type]['odata_path']}"
        f"(Id='{artifact_id}',Version='{version}')/$value"
    )

    for attempt in range(3):
        try:
            response = session.get(download_url, timeout=300)
            if response.status_code == 200:
                zip_bytes = response.content

                with tempfile.NamedTemporaryFile(
                    suffix=".zip", delete=False
                ) as temp_zip:
                    temp_zip.write(zip_bytes)
                    zip_file = temp_zip.name

                try:
                    with zipfile.ZipFile(zip_file) as zf:
                        # Validate zip file before extraction
                        if not zf.testzip():
                            zf.extractall(target_dir)
                            return True
                        else:
                            print(f"Warning: Corrupt zip for {artifact_id}")
                            return False
                finally:
                    os.remove(zip_file)
            elif response.status_code == 404:
                print(
                    f"{artifact_type} {artifact_id} not found (attempt {attempt + 1})"
                )
                break
            else:
                print(
                    f"Failed downloading {artifact_id}. HTTP {response.status_code} (attempt {attempt + 1})"
                )
        except Exception as e:
            print(f"Error downloading {artifact_id}: {str(e)} (attempt {attempt + 1})")

    return False


# =====================================================
# Download subobjects for each iflow (configuration, resources)
# =====================================================


def download_subobject(artifact_id, version, subobject_type):
    """
    Download a subobject (configuration or resource) for an artifact.
    Returns None if not found or failed, otherwise returns zip content.
    """
    # Try both OData v2 and v4 endpoints
    endpoints = [
        f"{SAP_HOST}/api/v1/IntegrationDesigntimeArtifacts('{artifact_id}',Version='{version}')/{subobject_type}/$value",
        f"{SAP_HOST}/api/v1/IntegrationDesigntimeArtifacts({artifact_id},Version='{version}')/{subobject_type}/$value",
    ]

    for endpoint in endpoints:
        try:
            response = session.get(endpoint, timeout=300)
            if response.status_code == 200:
                return response.content
            elif response.status_code != 404:
                print(
                    f"Warning: {subobject_type} for {artifact_id}: HTTP {response.status_code}"
                )
        except Exception as e:
            print(f"Error downloading {subobject_type} for {artifact_id}: {str(e)}")

    return None


def main():
    download_all_packages()
    git_add_and_push()


def download_all_packages():
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

        # =====================================================
        # Process all artifacts by type
        # =====================================================

        for artifact_type in ARTIFACT_TYPES.keys():
            artifacts_url = f"{SAP_HOST}/api/v1/IntegrationPackages('{package_id}')/{ARTIFACT_TYPES[artifact_type]['odata_path']}"
            response = session.get(artifacts_url, timeout=120)

            package_artifacts = []
            if response.status_code == 200:
                payload = response.json()
                package_artifacts = payload["d"]["results"]
                print(
                    f"Found {len(package_artifacts)} {artifact_type} artifacts in package {package_id}"
                )
            else:
                print(
                    f"Warning: Failed to get {artifact_type} artifacts for package {package_id}. HTTP {response.status_code}"
                )

            # Process each artifact
            for artifact in package_artifacts:
                artifact_id = artifact["Id"]
                version = artifact["Version"]
                package_id = artifact.get("PackageId", "")

                print(
                    f"Exporting {artifact_type} {artifact_id} ({version}) - Package: {package_id}, Type: {artifact_type}"
                )

                # Use organized path structure
                target_dir = get_artifact_path(artifact_id, package_id, artifact_type)

                clean_directory(target_dir)

                if not download_and_extract_artifact(
                    artifact_id, version, target_dir, artifact_type
                ):
                    print(f"Skipping {artifact_id} - Download failed after retries")
                    continue

                metadata = {
                    "id": artifact_id,
                    "version": version,
                    "packageId": package_id,
                    "type": artifact_type,
                    "path": str(target_dir.relative_to(ARTIFACTS_DIR)),
                    "timestamp": datetime.datetime.now().isoformat(),
                }

                with open(
                    target_dir / "artifact-metadata.json", "w", encoding="utf-8"
                ) as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)

    # print(f"Total integration artifacts found: {len(artifacts)}")


# =====================================================
# Git Operations (if gitpython is available)
# =====================================================


def git_add_and_push():
    if git:
        try:
            repo = git.Repo(search_parent_directories=False)
            current_branch = repo.active_branch.name

            # Create new branch for this backup
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            new_branch_name = f"{GIT_BRANCH_PREFIX}{timestamp}"

            try:
                repo.git.checkout("HEAD", b=new_branch_name)
            except git.exc.GitCommandError:  # pyright: ignore[reportAttributeAccessIssue]
                # Branch already exists, checkout existing
                repo.git.checkout(new_branch_name)

            # Add all changes and commit
            repo.git.add(ARTIFACTS_DIR, all=True)

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


if __name__ == "__main__":
    main()
