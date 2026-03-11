#!/usr/bin/env python3
"""
RunPod Serverless Handler — COLMAP + Brush 3DGS Pipeline

Accepts a job with an images ZIP URL, runs COLMAP SFM then Brush training,
and returns the resulting .ply file (uploaded to RunPod bucket or base64).
"""

import os
import sys
import base64
import shutil
import subprocess
import tempfile
import zipfile
from io import BytesIO

import requests
import runpod
from runpod.serverless.utils import rp_upload

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
COLMAP_EXE = "colmap"
BRUSH_EXE = "/brush-app-x86_64-unknown-linux-gnu/brush_app"

os.environ["QT_QPA_PLATFORM"] = "offscreen"


# ---------------------------------------------------------------------------
# Pipeline helpers (same logic as runpod_pod.py)
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


def run_brush(colmap_workspace: str, output_dir: str, brush_params: dict | None = None) -> str:
    """Run Brush training and return the path to the output PLY."""

    if brush_params is None:
        brush_params = {}

    print("--- Running Brush training ---")

    cmd = [
        "xvfb-run",
        "-a",
        BRUSH_EXE,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def download_and_extract_images(url: str, dest_dir: str) -> str:
    """Download a ZIP from *url*, extract it, and return the images directory."""

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
    """Upload the PLY via presigned PUT, RunPod bucket, or base64 (in priority order)."""

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

    # Priority 2: RunPod S3 bucket
    bucket_url = os.environ.get("BUCKET_ENDPOINT_URL")
    if bucket_url:
        print("--- Uploading PLY to RunPod bucket ---")
        presigned_url = rp_upload.upload_file_to_bucket(
            file_name="output.ply",
            file_location=ply_path,
        )
        return {"output_url": presigned_url}

    # Priority 3: base64 encode the PLY
    print("--- No bucket configured — encoding PLY as base64 ---")
    with open(ply_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return {"output_base64": encoded, "filename": "output.ply"}


# ---------------------------------------------------------------------------
# RunPod handler
# ---------------------------------------------------------------------------
def handler(job: dict) -> dict:
    """
    RunPod serverless handler.

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
    the RunPod bucket / base64 fallback.
    """

    job_input = job["input"]

    # Resolve download URL — presigned GET takes priority
    download_url = job_input.get("s3_presigned_get_url") or job_input.get("images_url")
    if not download_url:
        return {"error": "Missing required field: provide images_url or s3_presigned_get_url"}

    s3_presigned_put_url = job_input.get("s3_presigned_put_url")

    # Create a temp directory for the whole job
    job_dir = tempfile.mkdtemp(prefix="runpod_3dgs_")

    try:
        # 1. Download & extract images
        images_dir = os.path.join(job_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        images_dir = download_and_extract_images(download_url, images_dir)

        output_dir = os.path.join(job_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # 2. COLMAP SFM
        runpod.serverless.progress_update(job, "Running COLMAP SFM...")
        colmap_workspace = run_colmap(images_dir, output_dir)

        # 3. Brush training
        brush_params = {
            "iterations": job_input.get("iterations", 30000),
            "sh_degree": job_input.get("sh_degree", 3),
            "max_splats": job_input.get("max_splats"),
            "max_resolution": job_input.get("max_resolution"),
            "densify_threshold": job_input.get("densify_threshold"),
        }
        runpod.serverless.progress_update(job, "Running Brush training...")
        ply_path = run_brush(colmap_workspace, output_dir, brush_params)

        if not os.path.exists(ply_path):
            return {"error": f"PLY file not found at {ply_path}"}

        # 4. Upload / return result
        runpod.serverless.progress_update(job, "Uploading result...")
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
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    runpod.serverless.start({"handler": handler})
