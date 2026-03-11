# COLMAP + Brush — 3DGS Pipeline (RunPod Flash) (WIP)

> **⚠️ BETA Notice:** This implementation is currently in beta and not finished yet.

Generate 3D Gaussian Splatting (`.ply`) from a set of images using **COLMAP** for structure-from-motion and **Brush** for Gaussian Splat training. Deployed as a **RunPod Flash** endpoint — no Dockerfile required.

> ⚠️ This setup uses the **non-CUDA** (CPU-only) build of COLMAP installed via `apt-get`. SIFT feature extraction and matching run on the CPU, while only Brush training uses the GPU.

---

## What is Flash?

[RunPod Flash](https://github.com/runpod/flash-examples) is a Python framework that lets you run functions on RunPod's Serverless infrastructure with a single `@Endpoint` decorator. Write code locally, deploy globally — Flash handles provisioning, scaling, and routing automatically.

---

## Prerequisites

- Python 3.10+
- [`uv`](https://github.com/astral-sh/uv) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [RunPod account](https://runpod.io/console/signup) with API key

---

## Quick Start

### 1. Install dependencies

```bash
pip install runpod-flash requests
```

Or with `uv`:

```bash
uv pip install runpod-flash requests
```

### 2. Authenticate with RunPod

```bash
flash login
```

### 3. Run locally (development)

```bash
cd COLMAP-Brush/flash
flash run
```

Open [http://localhost:8888/docs](http://localhost:8888/docs) to explore the endpoint.

### 4. Deploy to RunPod

```bash
flash deploy
```

---

## API

### Endpoint: `colmap-brush-3dgs`

**Input:**

```json
{
  "images_url": "https://example.com/scene_images.zip",
  "s3_presigned_get_url": "https://my-bucket.s3.amazonaws.com/input.zip?X-Amz-...",
  "s3_presigned_put_url": "https://my-bucket.s3.amazonaws.com/output.ply?X-Amz-...",
  "iterations": 30000,
  "sh_degree": 3,
  "max_splats": null,
  "max_resolution": null,
  "densify_threshold": null
}
```

### Input Fields

| Field                  | Type    | Required | Default | Description                          |
| ---------------------- | ------- | -------- | ------- | ------------------------------------ |
| `images_url`           | string  | ✅*      | —       | URL to a `.zip` archive of images    |
| `s3_presigned_get_url` | string  | ✅*      | —       | S3 presigned GET URL (alternative to `images_url`) |
| `s3_presigned_put_url` | string  | —        | —       | S3 presigned PUT URL for output upload |
| `iterations`           | int     | —        | `30000` | Number of Brush training steps       |
| `sh_degree`            | int     | —        | `3`     | Spherical harmonics degree           |
| `max_splats`           | int     | —        | —       | Maximum number of splats             |
| `max_resolution`       | int     | —        | —       | Max image resolution for training    |
| `densify_threshold`    | float   | —        | —       | Densification gradient threshold     |

*\*At least one of `images_url` or `s3_presigned_get_url` must be provided. If `s3_presigned_get_url` is provided, it takes priority.*

### Response

**With Presigned PUT URL / S3 bucket configured:**

```json
{
  "output_url": "https://your-bucket.s3.amazonaws.com/3dgs/output.ply"
}
```

**Without bucket (base64 fallback):**

```json
{
  "output_base64": "<base64-encoded PLY>",
  "filename": "output.ply"
}
```

---

## Pipeline Steps

1. **Download & Extract** — fetches the images `.zip` from the provided URL
2. **COLMAP Feature Extraction** — extracts SIFT features from input images
3. **COLMAP Exhaustive Matching** — matches features across image pairs
4. **COLMAP Mapper** — reconstructs a sparse 3D model
5. **COLMAP Model Converter** — exports the model to TXT format for Brush
6. **Brush Training** — trains a 3D Gaussian Splat model and exports `output.ply`
7. **Upload / Return** — uploads PLY to S3 bucket or returns base64

---

## Output

The final output is a `.ply` file. You can view the result by uploading it to [SuperSplat](https://superspl.at/).

---

## How It Works

Flash handles deployment automatically. Under the hood:

| Feature              | Detail                                                  |
| -------------------- | ------------------------------------------------------- |
| GPU                  | `NVIDIA_GEFORCE_RTX_4090` (configurable in code)        |
| Auto-scaling         | 0 → 3 workers (scales to zero when idle)                |
| System dependencies  | `colmap`, `wget`, `xz-utils` (installed via apt)        |
| Python dependencies  | `requests` (pip-installed)                               |
| Brush binary         | Downloaded & cached on first invocation (~15 MB)         |

---

## CLI Commands

```bash
flash login       # Authenticate with RunPod (opens browser)
flash run         # Run development server (localhost:8888)
flash build       # Build deployment package
flash deploy      # Build and deploy to RunPod
flash undeploy    # Delete deployed endpoint
```
