#!/usr/bin/env python3
"""
RunPod Flash Endpoint — COLMAP + Brush 3DGS Pipeline

Accepts a job with an images ZIP URL, runs COLMAP SFM then Brush training,
and returns the resulting .ply file (base64-encoded or uploaded to S3).

Deploy with:
    flash login
    flash deploy
"""

import os
import shutil
import subprocess
import tempfile

from runpod_flash import Endpoint, GpuType

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
COLMAP_EXE = "colmap"
BRUSH_URL = "https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-unknown-linux-gnu.tar.xz"
BRUSH_INSTALL_DIR = "/tmp/brush"
BRUSH_EXE = os.path.join(BRUSH_INSTALL_DIR, "brush-app-x86_64-unknown-linux-gnu", "brush_app")


# ---------------------------------------------------------------------------
# One-time setup: download & cache the Brush binary
# ---------------------------------------------------------------------------
def ensure_brush_installed() -> str:
    """Download and extract Brush if not already present. Returns the exe path."""

    if os.path.isfile(BRUSH_EXE):
        return BRUSH_EXE

    print("--- Downloading Brush binary (first run only) ---")
    os.makedirs(BRUSH_INSTALL_DIR, exist_ok=True)
    archive_path = os.path.join(BRUSH_INSTALL_DIR, "brush.tar.xz")

    subprocess.run(
        ["wget", "-q", BRUSH_URL, "-O", archive_path],
        check=True,
    )
    subprocess.run(
        ["tar", "-xf", archive_path, "-C", BRUSH_INSTALL_DIR],
        check=True,
    )
    os.remove(archive_path)

    if not os.path.isfile(BRUSH_EXE):
        raise RuntimeError(f"Brush binary not found at {BRUSH_EXE} after extraction")

    print("--- Brush binary ready ---")
    return BRUSH_EXE


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------
def run_colmap(image_dir: str, output_dir: str) -> str:
    """Run COLMAP feature extraction → matching → mapping → TXT export."""

    workspace = os.path.join(output_dir, "colmap_workspace")
    os.makedirs(workspace, exist_ok=True)
    database_path = os.path.join(workspace, "database.db")
    sparse_dir = os.path.join(workspace, "sparse")
    os.makedirs(sparse_dir, exist_ok=True)

    print("--- Running COLMAP feature extraction ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "feature_extractor",
            "--database_path", database_path,
            "--image_path", image_dir,
            "--SiftExtraction.use_gpu", "0",
            "--SiftExtraction.peak_threshold", "0.01",
            "--ImageReader.single_camera", "1",
        ],
        check=True,
    )

    print("--- Running COLMAP matcher ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "exhaustive_matcher",
            "--database_path", database_path,
            "--SiftMatching.use_gpu", "0",
        ],
        check=True,
    )

    print("--- Running COLMAP mapper ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "mapper",
            "--database_path", database_path,
            "--image_path", image_dir,
            "--output_path", sparse_dir,
        ],
        check=True,
    )

    # Identify the first sparse model (usually "0")
    model_path = os.path.join(sparse_dir, "0")
    if not os.path.exists(model_path):
        subdirs = [
            d for d in os.listdir(sparse_dir)
            if os.path.isdir(os.path.join(sparse_dir, d))
        ]
        if subdirs:
            model_path = os.path.join(sparse_dir, subdirs[0])
        else:
            raise RuntimeError("COLMAP mapper failed to create a sparse model.")

    print("--- Running COLMAP model converter (to TXT for Brush) ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "model_converter",
            "--input_path", model_path,
            "--output_path", model_path,
            "--output_type", "txt",
        ],
        check=True,
    )

    # Symlink images into the workspace so Brush can find them
    workspace_images = os.path.join(workspace, "images")
    if not os.path.exists(workspace_images):
        print("--- Linking images to workspace ---")
        try:
            os.symlink(image_dir, workspace_images, target_is_directory=True)
        except OSError:
            print("--- Copying images to workspace (symlink failed) ---")
            shutil.copytree(image_dir, workspace_images)

    return workspace


def run_brush(
    brush_exe: str, colmap_workspace: str, output_dir: str, brush_params: dict | None = None,
) -> str:
    """Run Brush training and return the path to the output PLY."""

    if brush_params is None:
        brush_params = {}

    print("--- Running Brush training ---")

    cmd = [
        "xvfb-run",
        "-a",
        brush_exe,
        colmap_workspace,
        "--export-path", output_dir,
        "--export-name", "output.ply",
    ]

    total_steps = (
        brush_params.get("iterations")
        or brush_params.get("total_steps")
        or "30000"
    )
    cmd.extend(["--total-steps", str(total_steps)])

    if brush_params.get("max_splats"):
        cmd.extend(["--max-splats", str(brush_params["max_splats"])])
    if brush_params.get("sh_degree"):
        cmd.extend(["--sh-degree", str(brush_params["sh_degree"])])
    if brush_params.get("max_resolution"):
        val = brush_params["max_resolution"]
        if isinstance(val, list):
            cmd.extend(["--max-resolution", str(val[0])])
        else:
            cmd.extend(["--max-resolution", str(val)])
    if brush_params.get("densify_threshold"):
        cmd.extend(["--growth-grad-threshold", str(brush_params["densify_threshold"])])

    print(f"Brush command: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error_msg = f"Brush training failed with code {result.returncode}\n"
        error_msg += f"STDOUT:\n{result.stdout}\n"
        error_msg += f"STDERR:\n{result.stderr}\n"
        raise RuntimeError(error_msg)

    return os.path.join(output_dir, "output.ply")


def download_and_extract_images(url: str, dest_dir: str) -> str:
    """Download a ZIP from *url*, extract it, and return the images directory."""
    import zipfile
    from io import BytesIO

    import requests

    print(f"--- Downloading images from {url} ---")
    resp = requests.get(url, timeout=600)
    resp.raise_for_status()

    with zipfile.ZipFile(BytesIO(resp.content)) as zf:
        zf.extractall(dest_dir)

    # If the zip contained a single top-level folder, use that as the image dir
    entries = os.listdir(dest_dir)
    if len(entries) == 1 and os.path.isdir(os.path.join(dest_dir, entries[0])):
        return os.path.join(dest_dir, entries[0])
    return dest_dir


def upload_result(ply_path: str, s3_presigned_put_url: str | None = None) -> dict:
    """Upload the PLY via presigned PUT, S3 bucket, or base64 (in priority order)."""
    import base64
    import requests

    # Priority 1: caller-provided presigned PUT URL
    if s3_presigned_put_url:
        print("--- Uploading PLY via S3 presigned PUT URL ---")
        with open(ply_path, "rb") as f:
            resp = requests.put(
                s3_presigned_put_url,
                data=f,
                headers={"Content-Type": "application/octet-stream"},
                timeout=600,
            )
        resp.raise_for_status()
        # Return the URL without query-string (the signed params) for a clean reference
        clean_url = s3_presigned_put_url.split("?")[0]
        return {"output_url": clean_url, "upload_method": "s3_presigned_put"}

    # Priority 2: S3 bucket via env vars
    bucket_url = os.environ.get("BUCKET_ENDPOINT_URL")
    if bucket_url:
        print("--- Uploading PLY to bucket ---")
        try:
            import boto3

            s3 = boto3.client(
                "s3",
                endpoint_url=bucket_url,
                aws_access_key_id=os.environ.get("BUCKET_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("BUCKET_SECRET_ACCESS_KEY"),
            )
            bucket_name = os.environ.get("BUCKET_NAME", "runpod-outputs")
            s3_key = f"3dgs/{os.path.basename(ply_path)}"
            s3.upload_file(ply_path, bucket_name, s3_key)
            return {"output_url": f"{bucket_url}/{bucket_name}/{s3_key}"}
        except Exception as exc:
            print(f"--- Bucket upload failed ({exc}), falling back to base64 ---")

    # Priority 3: base64 encode the PLY
    print("--- Encoding PLY as base64 ---")
    with open(ply_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return {"output_base64": encoded, "filename": "output.ply"}


# ---------------------------------------------------------------------------
# Flash Endpoint
# ---------------------------------------------------------------------------
@Endpoint(
    name="colmap-brush-3dgs",
    gpu=GpuType.NVIDIA_GEFORCE_RTX_4090,
    workers=(0, 3),
    dependencies=["requests"],
    system_dependencies=["colmap", "wget", "xz-utils", "libgl1", "libglib2.0-0", "libvulkan1", "vulkan-tools", "mesa-vulkan-drivers", "xvfb"],
)
async def generate_3dgs(input_data: dict) -> dict:
    """
    Generate a 3D Gaussian Splat (.ply) from a set of images.

    Expected input:
    {
        "images_url":            str   — URL to a .zip of images
        "s3_presigned_get_url":  str   — S3 presigned GET URL for input ZIP (alternative to images_url)
        "s3_presigned_put_url":  str   — S3 presigned PUT URL for output PLY upload (optional)
        "iterations":            int   — training steps         (default 30000)
        "sh_degree":             int   — SH degree              (default 3)
        "max_splats":            int   — max splats              (optional)
        "max_resolution":        int   — max resolution          (optional)
        "densify_threshold":     float — densify grad threshold (optional)
    }

    At least one of images_url or s3_presigned_get_url must be provided.
    If s3_presigned_get_url is provided it takes priority over images_url.
    If s3_presigned_put_url is provided the result is uploaded there instead of
    the S3 bucket / base64 fallback.
    """

    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    # Resolve download URL — presigned GET takes priority
    download_url = input_data.get("s3_presigned_get_url") or input_data.get("images_url")
    if not download_url:
        return {"error": "Missing required field: provide images_url or s3_presigned_get_url"}

    s3_presigned_put_url = input_data.get("s3_presigned_put_url")

    # Ensure Brush is available (cached after first call)
    brush_exe = ensure_brush_installed()

    # Create a temp directory for the whole job
    job_dir = tempfile.mkdtemp(prefix="flash_3dgs_")

    try:
        # 1. Download & extract images
        images_dir = os.path.join(job_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        images_dir = download_and_extract_images(download_url, images_dir)

        output_dir = os.path.join(job_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # 2. COLMAP SFM
        colmap_workspace = run_colmap(images_dir, output_dir)

        # 3. Brush training
        brush_params = {
            "iterations": input_data.get("iterations", 30000),
            "sh_degree": input_data.get("sh_degree", 3),
            "max_splats": input_data.get("max_splats"),
            "max_resolution": input_data.get("max_resolution"),
            "densify_threshold": input_data.get("densify_threshold"),
        }
        ply_path = run_brush(brush_exe, colmap_workspace, output_dir, brush_params)

        if not os.path.exists(ply_path):
            return {"error": f"PLY file not found at {ply_path}"}

        # 4. Upload / return result
        result = upload_result(ply_path, s3_presigned_put_url=s3_presigned_put_url)
        return result

    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Subprocess failed: {exc}")
    except Exception as exc:
        raise RuntimeError(str(exc))
    finally:
        # 5. Clean up
        shutil.rmtree(job_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Local testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio

    test_payload = {
        "images_url": "https://example.com/scene_images.zip",
        "iterations": 1000,
        # Optional S3 presigned URLs:
        # "s3_presigned_get_url": "https://my-bucket.s3.amazonaws.com/input.zip?X-Amz-...",
        # "s3_presigned_put_url": "https://my-bucket.s3.amazonaws.com/output.ply?X-Amz-...",
    }
    print(f"Testing Flash endpoint with payload: {test_payload}")
    result = asyncio.run(generate_3dgs(test_payload))
    print(f"Result: {result}")
