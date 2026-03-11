import os
import time
import argparse
import requests
import uuid
import zipfile

try:
    import boto3
except ImportError:
    boto3 = None

def zip_directory(dir_path, zip_path):
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(dir_path):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, start=dir_path)
                zipf.write(file_path, arcname)

def generate_presigned_url(s3_client, client_method, method_parameters, expires_in=3600):
    try:
        url = s3_client.generate_presigned_url(
            ClientMethod=client_method,
            Params=method_parameters,
            ExpiresIn=expires_in
        )
        return url
    except Exception as e:
        print(f"Error generating presigned URL: {e}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Call RunPod Serverless Endpoint for 3DGS Pipeline")
    parser.add_argument("--endpoint", required=True, help="RunPod Serverless Endpoint ID")
    parser.add_argument("--images-url", help="URL to a .zip archive of images")
    parser.add_argument("--s3-get-url", help="S3 presigned GET URL for input ZIP (takes priority if both are set)")
    parser.add_argument("--s3-put-url", help="S3 presigned PUT URL for output upload")
    
    # New arguments for automatic S3 handling
    parser.add_argument("--input", help="Local directory or .zip file containing images to upload to S3")
    parser.add_argument("--s3-bucket", help="S3 bucket name to use for uploading inputs and outputs")
    parser.add_argument("--s3-prefix", default="3dgs-runs/", help="S3 prefix (folder) to use for uploading")
    parser.add_argument("--output-dir", default="output", help="Local directory to save the output .ply file")
    
    # AWS Credentials
    parser.add_argument("--aws-access-key-id", help="AWS Access Key ID (optional, defaults to environment/profile)")
    parser.add_argument("--aws-secret-access-key", help="AWS Secret Access Key (optional)")
    parser.add_argument("--aws-region", help="AWS Region (optional)")
    
    parser.add_argument("--iterations", type=int, default=30000, help="Number of Brush training steps")
    parser.add_argument("--sh-degree", type=int, default=3, help="Spherical harmonics degree")
    parser.add_argument("--api-key", help="RunPod API Key (defaults to RUNPOD_API_KEY env var)")

    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("Error: RunPod API key is required. Set RUNPOD_API_KEY environment variable or pass --api-key.")
        return

    input_payload = {
        "iterations": args.iterations,
        "sh_degree": args.sh_degree,
    }

    s3_client = None
    output_s3_key = None

    if args.input:
        if not boto3:
            print("Error: boto3 is not installed. Run 'pip install boto3' to use --input with S3.")
            return
        if not args.s3_bucket:
            print("Error: --s3-bucket is required when using --input.")
            return
        
        s3_kwargs = {}
        if args.aws_region:
            s3_kwargs['region_name'] = args.aws_region
        if args.aws_access_key_id and args.aws_secret_access_key:
            s3_kwargs['aws_access_key_id'] = args.aws_access_key_id
            s3_kwargs['aws_secret_access_key'] = args.aws_secret_access_key

        s3_client = boto3.client('s3', **s3_kwargs)
        run_id = str(uuid.uuid4())
        input_s3_key = f"{args.s3_prefix}{run_id}/input.zip"
        output_s3_key = f"{args.s3_prefix}{run_id}/output.ply"

        upload_path = args.input
        is_temp_zip = False
        
        if os.path.isdir(args.input):
            print(f"Zipping directory {args.input}...")
            upload_path = f"temp_input_{run_id}.zip"
            zip_directory(args.input, upload_path)
            is_temp_zip = True
        elif not os.path.exists(args.input):
            print(f"Error: Input path {args.input} does not exist.")
            return
            
        print(f"Uploading {upload_path} to s3://{args.s3_bucket}/{input_s3_key}...")
        try:
            s3_client.upload_file(upload_path, args.s3_bucket, input_s3_key)
        except Exception as e:
            print(f"Error uploading file to S3: {e}")
            if is_temp_zip and os.path.exists(upload_path):
                os.remove(upload_path)
            return
            
        if is_temp_zip and os.path.exists(upload_path):
            os.remove(upload_path)

        print("Generating presigned URLs...")
        get_url = generate_presigned_url(
            s3_client, 'get_object', 
            {'Bucket': args.s3_bucket, 'Key': input_s3_key}, 3600*24
        )
        put_url = generate_presigned_url(
            s3_client, 'put_object', 
            {'Bucket': args.s3_bucket, 'Key': output_s3_key, 'ContentType': 'application/octet-stream'}, 3600*24
        )
        
        if not get_url or not put_url:
            print("Error: Failed to generate presigned URLs.")
            return
            
        input_payload["s3_presigned_get_url"] = get_url
        input_payload["s3_presigned_put_url"] = put_url

    else:
        if not args.images_url and not args.s3_get_url:
            print("Error: You must provide either --input (with --s3-bucket), --images-url, or --s3-get-url.")
            return

        if args.s3_get_url:
            input_payload["s3_presigned_get_url"] = args.s3_get_url
        elif args.images_url:
            input_payload["images_url"] = args.images_url

        if args.s3_put_url:
            input_payload["s3_presigned_put_url"] = args.s3_put_url

    # Clean up endpoint if user passed full URL
    endpoint_id = args.endpoint.rstrip('/')
    if endpoint_id.startswith("http"):
        # e.g. https://api.runpod.ai/v2/abc123xyz
        endpoint_id = endpoint_id.split("/")[-1]
        
    print(f"Creating job on endpoint {endpoint_id}...")
    
    run_url = f"https://api.runpod.ai/v2/{endpoint_id}/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(run_url, headers=headers, json={"input": input_payload})
        response.raise_for_status()
        
        run_data = response.json()
        job_id = run_data.get("id")
        
        if not job_id:
            print(f"Error: Job ID not found in response: {run_data}")
            return
            
        print(f"Job created! Job ID: {job_id}")
        print("Waiting for completion...")
        
        status_url = f"https://api.runpod.ai/v2/{endpoint_id}/status/{job_id}"
        
        while True:
            status_res = requests.get(status_url, headers=headers)
            status_res.raise_for_status()
            
            status_data = status_res.json()
            status = status_data.get("status")
            
            print(f"Status: {status}")
            
            if status == "COMPLETED":
                output_data = status_data.get("output", {})
                
                # Check if the output is an error dictionary despite status being COMPLETED
                if isinstance(output_data, dict) and "error" in output_data:
                    print(f"\nJob failed (RunPod reported COMPLETED with error output).")
                    print(f"Error details: {output_data.get('error')}")
                    return

                print(f"\nJob completed successfully!")
                print("Result:")
                print(output_data)
                
                # Download logic if we used S3
                if args.input and args.s3_bucket and output_s3_key and s3_client:
                    os.makedirs(args.output_dir, exist_ok=True)
                    download_path = os.path.join(args.output_dir, "output.ply")
                    print(f"Downloading result from s3://{args.s3_bucket}/{output_s3_key} to {download_path}...")
                    try:
                        s3_client.download_file(args.s3_bucket, output_s3_key, download_path)
                        print(f"Download complete! File saved successfully to {download_path}")
                    except Exception as e:
                        print(f"Error downloading output from S3: {e}")
                
                return
            elif status in ["FAILED", "CANCELLED", "TIMED_OUT"]:
                print(f"\nJob ended with status {status}.")
                if "error" in status_data:
                    print(f"Error details: {status_data.get('error')}")
                return
                
            # Wait before polling again
            time.sleep(5)
                
    except Exception as e:
        print(f"Error calling endpoint: {e}")

if __name__ == "__main__":
    main()
