import os
from dotenv import load_dotenv

load_dotenv()

required = ["PFP_BASE_URL", "PFP_USERNAME", "PFP_PASSWORD", "PFP_DOWNLOAD_DIR"]
missing = [k for k in required if not os.getenv(k)]

if missing:
    raise SystemExit(f"Missing env vars: {missing}")

print("OK. Env loaded.")
print("BASE:", os.getenv("PFP_BASE_URL"))
print("DOWNLOAD_DIR:", os.getenv("PFP_DOWNLOAD_DIR"))
