# COLMAP + Brush — 3DGS Pipeline (RunPod)

Generate 3D Gaussian Splatting (`.ply`) from a set of images using **COLMAP** for structure-from-motion and **Brush** for Gaussian Splat training. Designed to run on a RunPod GPU pod.

> ⚠️ This setup uses the **non-CUDA** (CPU-only) build of COLMAP installed via `apt-get`. SIFT feature extraction and matching run on the CPU, while only Brush training uses the GPU.

---

## Prerequisites

- A **RunPod GPU pod** — a CUDA-specific base image is **not** required; any Ubuntu-based template will work
  - Example: `runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404` or a plain Ubuntu image
- Input images of the scene you want to reconstruct

---

## Setup

### 1. Clone the repository

```bash
cd /workspace
git clone https://github.com/KokkakNiphon/3DGS-Runpod.git
```

### 2. Install COLMAP

```bash
apt-get update && apt-get install colmap -y
```

### 3. Install Brush

Download and extract the Brush app binary:

```bash
cd /workspace
wget https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-unknown-linux-gnu.tar.xz
tar -xf brush-app-x86_64-unknown-linux-gnu.tar.xz
```

The binary will be located at `/workspace/brush-app-x86_64-unknown-linux-gnu/brush_app`.

---

## Usage

### Run the pipeline

```bash
python runpod_pod.py <image_dir> [options]
```

### Arguments

| Argument         | Description                              | Default   |
| ---------------- | ---------------------------------------- | --------- |
| `image_dir`      | Directory containing input images        | required  |
| `--output_dir`   | Directory to save outputs                | `./output`|
| `--iterations`   | Number of training iterations            | `30000`   |
| `--sh_degree`    | Spherical harmonics degree               | `3`       |

### Example

```bash
python runpod_pod.py /workspace/my_images --output_dir /workspace/output --iterations 30000
```

---

## Pipeline Steps

1. **COLMAP Feature Extraction** — extracts SIFT features from input images
2. **COLMAP Exhaustive Matching** — matches features across image pairs
3. **COLMAP Mapper** — reconstructs a sparse 3D model
4. **COLMAP Model Converter** — exports the model to TXT format for Brush
5. **Brush Training** — trains a 3D Gaussian Splat model and exports `output.ply`

---

## Output

The final output is a `.ply` file located at `<output_dir>/output.ply`.

You can view the result online by uploading the `.ply` file to [SuperSplat](https://superspl.at/).
