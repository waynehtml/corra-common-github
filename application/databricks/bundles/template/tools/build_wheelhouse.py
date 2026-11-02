# Databricks notebook source
# MAGIC %md
# MAGIC ## This notebook will build a wheelhouse for Nexus caching. 
# MAGIC Run the job with following parameters 
# MAGIC - task: tools/build_wheelhouse
# MAGIC - mode: sys

# COMMAND ----------

# MAGIC %run ../common

# COMMAND ----------

# Standard libraries
import logging
# Get logger
logger = logging.getLogger(__name__)

# Get job context
job_ctx = get_job_context()
logger.debug(f"job_ctx: {job_ctx}")

# COMMAND ----------

from pathlib import Path
import shlex
import subprocess
from urllib.parse import urlparse

def run_cmd(cmd: list[str]) -> None:
    print("Running:", " ".join(shlex.quote(str(part)) for part in cmd))
    result = subprocess.run([str(part) for part in cmd], capture_output=True, text=True)

    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)

    result.check_returncode()

def build_wheelhouse(
    req: str,
    wheelhouse: str,
    nexus_url: str,
) -> None:
    nexus_host = urlparse(nexus_url).hostname
    if not nexus_host:
        raise ValueError(f"Unable to extract Nexus host from nexus_url: {nexus_url}")

    logger.info(f"REQ_PATH={req}")
    logger.info(f"WHEELHOUSE={wheelhouse}")
    logger.info(f"NEXUS_URL={nexus_url}")
    logger.info(f"NEXUS_HOST={nexus_host}")

    req_path = Path(req)
    if not req_path.exists():
        raise FileNotFoundError(f"requirements.txt not found: {req_path}")

    # Clean up wheelhouse folder
    try:
        logger.info(f"Clean up wheelhouse at: {wheelhouse}")
        dbutils.fs.rm(wheelhouse, True)
        logger.info("Done.")
    except Exception:
        logger.info(f"Wheelhouse folder may not exist or is already removed: {wheelhouse}")

    # Create wheelhouse folder
    wheelhouse_path = Path(wheelhouse)
    wheelhouse_path.mkdir(parents=True, exist_ok=True)

    logger.info("Building wheelhouse ...")
    run_cmd(["pip", "config", "set", "global.trusted-host", nexus_host])
    run_cmd([
        "pip",
        "download",
        "--dest", str(wheelhouse_path),
        "--index-url", nexus_url,
        "-r", str(req_path),
    ])

# COMMAND ----------

# Get job parameters
CLUSTER_INIT_PATH = job_ctx.get("cluster_init_path")
logger.info(f"cluster_init_path is {CLUSTER_INIT_PATH}")
CLUSTER_WHEELHOUSE_PATH = job_ctx.get("cluster_wheelhouse_path")
logger.info(f"cluster_wheelhouse_path is {CLUSTER_WHEELHOUSE_PATH}")
NEXUS_URL = job_ctx.get("nexus_url")
logger.info(f"NEXUS_URL is {NEXUS_URL}")

# Compose
# requirments.txt file path
req_file = f"{CLUSTER_INIT_PATH}/requirements.txt"
logger.info(f"req_file is {req_file}")
# wheelhouse path
wheelhouse_dir = CLUSTER_WHEELHOUSE_PATH
logger.info(f"wheelhouse_dir is {wheelhouse_dir}")

# Build
build_wheelhouse(
    req=req_file,
    wheelhouse=wheelhouse_dir,
    nexus_url=NEXUS_URL,
)
