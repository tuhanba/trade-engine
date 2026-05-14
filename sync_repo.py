import urllib.request
import zipfile
import os
import shutil

url = "https://github.com/tuhanba/trade-engine/archive/refs/heads/main.zip"
zip_path = "repo_temp.zip"
target_dir = r"c:\Users\pc\Desktop\AURVEX Ai"

print(f"Downloading repository from {url}...")
try:
    urllib.request.urlretrieve(url, zip_path)
except Exception as e:
    print(f"Error downloading: {e}")
    exit(1)

print("Extracting files...")
try:
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall("repo_temp_extracted")
except Exception as e:
    print(f"Error extracting: {e}")
    exit(1)

# GitHub zips usually contain a root folder like 'repo-name-branch'
extracted_root = os.path.join("repo_temp_extracted", "trade-engine-main")

if not os.path.exists(extracted_root):
    # Fallback in case the branch name is different or folder structure varies
    dirs = os.listdir("repo_temp_extracted")
    if len(dirs) == 1:
        extracted_root = os.path.join("repo_temp_extracted", dirs[0])
    else:
        print("Could not find the root folder in the extracted zip.")
        exit(1)

print("Applying files to the workspace...")
for root, dirs, files in os.walk(extracted_root):
    for file in files:
        src_file = os.path.join(root, file)
        # Calculate relative path to the root of the extracted repo
        rel_path = os.path.relpath(src_file, extracted_root)
        dst_file = os.path.join(target_dir, rel_path)
        
        # Ensure target directory exists
        os.makedirs(os.path.dirname(dst_file), exist_ok=True)
        shutil.copy2(src_file, dst_file)
        print(f"Updated: {rel_path}")

print("Cleaning up temporary files...")
try:
    os.remove(zip_path)
    shutil.rmtree("repo_temp_extracted")
except Exception as e:
    print(f"Warning during cleanup: {e}")

print("\nSuccess! The repository has been fully applied to your workspace.")
