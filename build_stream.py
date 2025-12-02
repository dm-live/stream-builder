cat << 'EOF' > /workspace/scripts/build_stream.py
import os
import math
import random
import requests
import subprocess

# 30 minutes target
TARGET_DURATION = 1800  # seconds

# Chunk size for TUS upload (10 MB)
CHUNK_SIZE = 10 * 1024 * 1024


def download_scenes(mood: str, num_scenes: int):
    mood_dir = f"/workspace/moods/{mood}"
    os.makedirs(mood_dir, exist_ok=True)

    print(f"Downloading scenes for mood: {mood}")

    scene_paths = []
    for i in range(1, num_scenes + 1):
        url = f"https://media.dm.live/moods/{mood}/scenes/scene_{i}.mp4"
        local_path = f"{mood_dir}/scene_{i}.mp4"

        print(f"> Downloading scene {i}: {url}")
        resp = requests.get(url)
        if resp.status_code != 200:
            print(f"  -> Failed: HTTP {resp.status_code}")
            continue

        with open(local_path, "wb") as f:
            f.write(resp.content)

        print(f"  Saved: {local_path}")
        scene_paths.append(local_path)

    if not scene_paths:
        raise RuntimeError("No scenes downloaded. Check URLs / scene count.")

    print(f"✓ Download complete: {len(scene_paths)} scene(s)")
    return scene_paths


def build_random_playlist(scene_paths):
    playlist = []
    total_duration = 0

    print("Building randomized playlist…")

    while total_duration < TARGET_DURATION:
        random.shuffle(scene_paths)
        playlist.extend(scene_paths)
        total_duration += len(scene_paths) * 10

    print(f"Playlist approx duration: {total_duration}s")
    return playlist


def write_concat_file(playlist, mood):
    concat_path = f"/workspace/moods/{mood}/concat_list.txt"
    with open(concat_path, "w") as f:
        for clip in playlist:
            f.write(f"file '{clip}'\n")

    print(f"Concat file written: {concat_path}")
    return concat_path


def generate_stream(concat_file, mood):
    os.makedirs("/workspace/output", exist_ok=True)
    output_path = f"/workspace/output/{mood}_30min.mp4"

    print(f"Encoding final stream for {mood} (30min high quality)…")

    cmd = [
        "ffmpeg",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-t", str(TARGET_DURATION),
        "-c:v", "libx264",
        "-crf", "12",
        "-preset", "veryslow",
        "-an",
        output_path,
    ]

    subprocess.run(cmd, check=True)
    print(f"✓ Stream created: {output_path}")
    return output_path


def initiate_tus_upload(filepath: str):
    account_id = os.getenv("CF_ACCOUNT_ID")
    token = os.getenv("CF_STREAM_TOKEN")

    file_size = os.path.getsize(filepath)
    print(f"File size: {file_size} bytes")

    create_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/stream"
    headers = {
        "Authorization": f"Bearer {token}",
        "Tus-Resumable": "1.0.0",
        "Upload-Length": str(file_size),
    }

    print("Initiating TUS upload session…")
    resp = requests.post(create_url, headers=headers)
    print("Init resp:", resp.status_code)

    if resp.status_code not in (200, 201):
        raise RuntimeError(f"TUS init failed: {resp.status_code} {resp.text}")

    upload_url = resp.headers.get("Location")
    video_id = resp.headers.get("stream-media-id")

    print(f"Upload URL: {upload_url}")
    print(f"Video ID: {video_id}")
    return upload_url, video_id, file_size


def upload_file_via_tus(filepath: str):
    token = os.getenv("CF_STREAM_TOKEN")

    upload_url, video_id, file_size = initiate_tus_upload(filepath)

    total_chunks = math.ceil(file_size / CHUNK_SIZE)
    offset = 0
    index = 0

    with open(filepath, "rb") as f:
        while offset < file_size:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            chunk_len = len(chunk)
            index += 1

            headers = {
                "Authorization": f"Bearer {token}",
                "Tus-Resumable": "1.0.0",
                "Content-Type": "application/offset+octet-stream",
                "Upload-Offset": str(offset),
            }

            print(f"Uploading chunk {index}/{total_chunks}")
            resp = requests.patch(upload_url, headers=headers, data=chunk)

            if resp.status_code not in (200, 201, 204):
                print(f"Chunk {index} error: {resp.status_code} - {resp.text}")
                raise RuntimeError(f"TUS failed at chunk {index}")

            offset = int(resp.headers.get("Upload-Offset", offset + chunk_len))
            print(f"  Uploaded: {(offset / file_size) * 100:.2f}%")

    print("✓ Upload complete")
    return {"video_id": video_id, "size": file_size}


def main():
    mood = input("Mood (lowercase): ").strip()
    num = int(input("Scene count: ").strip())

    scenes = download_scenes(mood, num)
    playlist = build_random_playlist(scenes)
    concat = write_concat_file(playlist, mood)
    output = generate_stream(concat, mood)
    result = upload_file_via_tus(output)

    print("Upload result:", result)


if __name__ == "__main__":
    main()
EOF
