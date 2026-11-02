# Databricks notebook source
# MAGIC %md
# MAGIC This notebook will set up SSH/SFTP server. 
# MAGIC Run the OEDS-DIS-JOB job with following parameters 
# MAGIC - config: /Workspace/oedataservices/.bundle/oedis/files/tools/ssh/test_download

# COMMAND ----------

# DBTITLE 1,download a test file
import os
import stat
import paramiko

host = "localhost"
port = 22
username = "test"

private_key_path = "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/ssh/key/id_rsa"
private_key_passphrase = "key_pass"

# Set this if you know the file name, otherwise leave as None
remote_file_name = None

local_download_dir = "/tmp/sftp_downloads"
os.makedirs(local_download_dir, exist_ok=True)

transport = None
sftp = None

try:
    pkey = paramiko.RSAKey.from_private_key_file(
        private_key_path,
        password=private_key_passphrase,
    )

    transport = paramiko.Transport((host, port))
    transport.connect(username=username, pkey=pkey)
    sftp = paramiko.SFTPClient.from_transport(transport)

    print("Connected to SFTP")

    remote_items = sftp.listdir_attr(".")
    files_only = [x for x in remote_items if not stat.S_ISDIR(x.st_mode)]

    if remote_file_name is None:
        if not files_only:
            raise FileNotFoundError("No file found in remote SFTP root")
        remote_file_name = files_only[0].filename

    local_file_path = os.path.join(local_download_dir, remote_file_name)

    print(f"Downloading remote file: {remote_file_name}")
    sftp.get(remote_file_name, local_file_path)

    print(f"Downloaded to: {local_file_path}")
    print("Downloaded files in local folder:", os.listdir(local_download_dir))

finally:
    if sftp is not None:
        sftp.close()
    if transport is not None:
        transport.close()
