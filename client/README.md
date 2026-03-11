# RunPod Serverless Client

This folder contains a Python script to call the RunPod Serverless Endpoint for the 3DGS (COLMAP + Brush) pipeline.

## Prerequisites

1. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Get your RunPod API key from the [RunPod Settings](https://www.runpod.io/console/settings).
3. Set your API key as an environment variable (or pass it via `--api-key`):
   ```bash
   export RUNPOD_API_KEY="your-api-key"
   ```
4. Configure your AWS credentials if using the automatic S3 uploading feature:
   ```bash
   export AWS_ACCESS_KEY_ID="your-access-key"
   export AWS_SECRET_ACCESS_KEY="your-secret-key"
   export AWS_REGION="your-region"
   ```

## Usage

You can call the endpoint using the `client.py` script. The script uses standard HTTP requests to submit a job and poll for the result.

### Example: Automatic S3 Zipping and Uploading (Recommended)

If you have a local folder of images, you can let `client.py` zip it, upload it to S3, generate presigned URLs, and automatically download the resulting `.ply` file when finished.

```bash
python client.py \
  --endpoint <your-endpoint-id> \
  --input ./path/to/my_images \
  --s3-bucket my-aws-s3-bucket-name \
  --output-dir ./results \
  --iterations 30000 \
  --sh-degree 3
```

### Quick Test with Provided Dataset

You can quickly test your endpoint using the provided `inputs/` folder, which contains a small example dataset.

- **300 iterations** (Quick test): Takes ~30 seconds to complete
- **5000 iterations** (Better quality): Takes ~90 seconds to complete

```bash
python client.py \
  --endpoint <your-endpoint-id> \
  --input ./inputs \
  --s3-bucket my-aws-s3-bucket-name \
  --iterations 300
```

### Example: Using S3 Presigned GET / PUT URLs manually

If your input images are in a private S3 bucket and you want the resulting `.ply` file to be uploaded back to your bucket instead of the RunPod bucket or Base64, you can use presigned URLs directly:

```bash
python client.py \
  --endpoint <your-endpoint-id> \
  --s3-get-url "https://my-bucket.s3.amazonaws.com/input.zip?X-Amz-..." \
  --s3-put-url "https://my-bucket.s3.amazonaws.com/output.ply?X-Amz-..." \
  --iterations 30000 \
  --sh-degree 3
```

### Example: Using a public ZIP URL

```bash
python client.py \
  --endpoint <your-endpoint-id> \
  --images-url "https://example.com/scene_images.zip" \
  --iterations 30000 \
  --sh-degree 3
```

## Arguments

| Argument | Description | Default |
| --- | --- | --- |
| `--endpoint` | **(Required)** RunPod Serverless Endpoint ID | - |
| `--input` | Local directory or `.zip` file containing images to upload to S3 | - |
| `--s3-bucket` | S3 bucket name to use for uploading inputs and outputs | - |
| `--s3-prefix` | S3 prefix (folder) to use for uploading | `3dgs-runs/` |
| `--output-dir` | Local directory to save the output `.ply` file | `output` |
| `--images-url` | URL to a `.zip` archive of images | - |
| `--s3-get-url` | S3 presigned GET URL for input ZIP | - |
| `--s3-put-url` | S3 presigned PUT URL for output upload | - |
| `--iterations` | Number of Brush training steps | `30000` |
| `--sh-degree` | Spherical harmonics degree | `3` |
| `--api-key` | RunPod API Key (defaults to `RUNPOD_API_KEY` env var) | - |

*Note: You must provide either `--input` (with `--s3-bucket`), `--images-url`, or `--s3-get-url`.*
