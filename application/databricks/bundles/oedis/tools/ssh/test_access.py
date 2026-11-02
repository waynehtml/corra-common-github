# Databricks notebook source
# MAGIC %md
# MAGIC This notebook will set up SSH/SFTP server. 
# MAGIC Run the OEDS-DIS-JOB job with following parameters 
# MAGIC - config: /Workspace/oedataservices/.bundle/oedis/files/tools/ssh/test_access

# COMMAND ----------

# DBTITLE 1,Test SFTP Access
import paramiko

host = "localhost"
port = 22
username = "test"

private_key_path = "/Volumes/dev_application_cat/oedisint_db/land-oedataservices/ssh/key/id_rsa"
private_key_passphrase = "key_pass"

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

    print("Connected successfully")
    print("Current directory listing:")
    for item in sftp.listdir_attr("."):
        print(f"- {item.filename}")

finally:
    if sftp is not None:
        sftp.close()
    if transport is not None:
        transport.close()
