#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import shutil

# Configuration for local binary paths
COLMAP_EXE = "colmap"
BRUSH_EXE = "/workspace/brush-app-x86_64-unknown-linux-gnu/brush_app"

os.environ["QT_QPA_PLATFORM"] = "offscreen"


def run_colmap(image_dir, output_dir):
    workspace = os.path.join(output_dir, "colmap_workspace")
    os.makedirs(workspace, exist_ok=True)
    database_path = os.path.join(workspace, "database.db")
    sparse_dir = os.path.join(workspace, "sparse")
    os.makedirs(sparse_dir, exist_ok=True)

    print(f"--- Running COLMAP feature extraction ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "feature_extractor",
            "--database_path",
            database_path,
            "--image_path",
            image_dir,
            "--SiftExtraction.use_gpu",
            "0",
            "--SiftExtraction.peak_threshold",
            "0.01",
            "--ImageReader.single_camera",
            "1",
        ],
        check=True,
    )

    print(f"--- Running COLMAP matcher ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "exhaustive_matcher",
            "--database_path",
            database_path,
            "--SiftMatching.use_gpu",
            "0",
        ],
        check=True,
    )

    print(f"--- Running COLMAP mapper ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "mapper",
            "--database_path",
            database_path,
            "--image_path",
            image_dir,
            "--output_path",
            sparse_dir,
        ],
        check=True,
    )

    # Identify the first sparse model (usually '0')
    model_path = os.path.join(sparse_dir, "0")
    if not os.path.exists(model_path):
        # Check if it created it directly in sparse_dir or another subfolder
        subdirs = [
            d
            for d in os.listdir(sparse_dir)
            if os.path.isdir(os.path.join(sparse_dir, d))
        ]
        if subdirs:
            model_path = os.path.join(sparse_dir, subdirs[0])
        else:
            raise RuntimeError("COLMAP mapper failed to create a sparse model.")

    print(f"--- Running COLMAP model converter (to TXT for Brush) ---")
    subprocess.run(
        [
            COLMAP_EXE,
            "model_converter",
            "--input_path",
            model_path,
            "--output_path",
            model_path,
            "--output_type",
            "txt",
        ],
        check=True,
    )

    # Create an 'images' symlink or copy images to the workspace for Brush
    workspace_images = os.path.join(workspace, "images")
    if not os.path.exists(workspace_images):
        print(f"--- Linking images to workspace ---")
        try:
            os.symlink(image_dir, workspace_images, target_is_directory=True)
        except OSError:
            # Fallback to copying if symlink fails (e.g. no permissions)
            print(f"--- Copying images to workspace (symlink failed) ---")
            shutil.copytree(image_dir, workspace_images)

    return workspace


def run_brush(colmap_workspace, output_dir, brush_params=None):
    if brush_params is None:
        brush_params = {}

    print(f"--- Running Brush training ---")

    cmd = [
        BRUSH_EXE,
        colmap_workspace,
        "--export-path",
        output_dir,
        "--export-name",
        "output.ply",
    ]

    # Map parameters
    total_steps = (
        brush_params.get("iterations") or brush_params.get("total_steps") or "30000"
    )
    cmd.extend(["--total-steps", str(total_steps)])

    if brush_params.get("max_splats"):
        cmd.extend(["--max-splats", str(brush_params["max_splats"])])

    if brush_params.get("sh_degree"):
        cmd.extend(["--sh-degree", str(brush_params["sh_degree"])])

    if brush_params.get("max_resolution"):
        max_res = brush_params["max_resolution"]
        if isinstance(max_res, list):
            cmd.extend(["--max-resolution", str(max_res[0])])
        else:
            cmd.extend(["--max-resolution", str(max_res)])

    if brush_params.get("densify_threshold"):
        cmd.extend(["--growth-grad-threshold", str(brush_params["densify_threshold"])])

    print(f"Brush command: {' '.join(cmd)}")

    # Brush app usually expects images in the same relative path or --image-path
    # The original script didn't pass --image-path to brush, but Brush might need it
    # depending on how the COLMAP model was exported.
    # Let's assume it works like the original environment for now.

    result = subprocess.run(cmd, capture_output=False, text=True)

    if result.returncode != 0:
        raise RuntimeError("Brush training failed")

    return os.path.join(output_dir, "output.ply")


def main():
    parser = argparse.ArgumentParser(description="Local 3DGS Pipeline (COLMAP + Brush)")
    parser.add_argument("image_dir", help="Directory containing input images")
    parser.add_argument(
        "--output_dir", help="Directory to save outputs (defaults to './output')"
    )
    parser.add_argument(
        "--iterations", type=int, default=30000, help="Number of training iterations"
    )
    parser.add_argument(
        "--sh_degree", type=int, default=3, help="Spherical harmonics degree"
    )

    args = parser.parse_args()

    image_dir = os.path.abspath(args.image_dir)
    output_dir = (
        os.path.abspath(args.output_dir)
        if args.output_dir
        else os.path.join(os.getcwd(), "output")
    )

    if not os.path.exists(image_dir):
        print(f"Error: Image directory not found: {image_dir}")
        sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)

    try:
        # Step 1: SFM with COLMAP
        colmap_model_path = run_colmap(image_dir, output_dir)

        # Step 2: Training with Brush
        brush_params = {"iterations": args.iterations, "sh_degree": args.sh_degree}
        ply_file = run_brush(colmap_model_path, output_dir, brush_params)

        print("\nPipeline complete!")
        print(f"Resulting PLY: {ply_file}")

    except Exception as e:
        print(f"\nAn error occurred: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
