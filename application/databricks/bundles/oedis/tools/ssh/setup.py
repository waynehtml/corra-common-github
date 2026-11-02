# Databricks notebook source
# MAGIC %md
# MAGIC This notebook will set up SSH/SFTP server. 
# MAGIC Run the OEDS-DIS-JOB job with following parameters 
# MAGIC - config: /Workspace/oedataservices/.bundle/oedis/files/tools/ssh/setup

# COMMAND ----------

# MAGIC %sh
# MAGIC set -e
# MAGIC
# MAGIC USER_NAME="test"
# MAGIC USER_PASS="user_pass"
# MAGIC KEY_DIR="/Volumes/dev_application_cat/oedisint_db/land-oedataservices/ssh/key"
# MAGIC SRC_DIR="/Volumes/dev_application_cat/oedisint_db/land-oedataservices/ssh/sftp"
# MAGIC
# MAGIC echo "=== Step 1: User setup ==="
# MAGIC if id "$USER_NAME" >/dev/null 2>&1; then
# MAGIC   echo "User $USER_NAME already exists. Skip user and key setup."
# MAGIC   USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
# MAGIC else
# MAGIC   echo "User $USER_NAME does not exist. Create user and set up keys."
# MAGIC   useradd -m -s /bin/bash "$USER_NAME"
# MAGIC   echo "$USER_NAME:$USER_PASS" | chpasswd
# MAGIC
# MAGIC   PUB_KEY_FILE=$(find "$KEY_DIR" -name "*.pub" | head -n 1)
# MAGIC   if [ -z "$PUB_KEY_FILE" ]; then
# MAGIC     echo "No public key found in $KEY_DIR"
# MAGIC     exit 1
# MAGIC   fi
# MAGIC
# MAGIC   USER_HOME=$(getent passwd "$USER_NAME" | cut -d: -f6)
# MAGIC   mkdir -p "$USER_HOME/.ssh"
# MAGIC   cp "$PUB_KEY_FILE" "$USER_HOME/.ssh/authorized_keys"
# MAGIC
# MAGIC   chmod 700 "$USER_HOME/.ssh"
# MAGIC   chmod 600 "$USER_HOME/.ssh/authorized_keys"
# MAGIC   chown -R "$USER_NAME:$USER_NAME" "$USER_HOME/.ssh"
# MAGIC fi
# MAGIC
# MAGIC echo "=== Step 2: Copy sftp files ==="
# MAGIC if [ ! -d "$SRC_DIR" ]; then
# MAGIC   echo "Source folder not found: $SRC_DIR"
# MAGIC   exit 1
# MAGIC fi
# MAGIC
# MAGIC DEST_DIR="$USER_HOME/sftp"
# MAGIC mkdir -p "$DEST_DIR"
# MAGIC rm -rf "$DEST_DIR"/*
# MAGIC cp -R "$SRC_DIR"/. "$DEST_DIR"/
# MAGIC chown -R "$USER_NAME:$USER_NAME" "$DEST_DIR"
# MAGIC chmod -R u+rwX "$DEST_DIR"
# MAGIC
# MAGIC echo "Copied from: $SRC_DIR to $DEST_DIR. The sftp files:"
# MAGIC find "$DEST_DIR" -maxdepth 3 -print
# MAGIC
# MAGIC echo "=== Step 3.1: Prepare SSH host keys/runtime ==="
# MAGIC ssh-keygen -A
# MAGIC mkdir -p /var/run/sshd
# MAGIC chmod 755 /var/run/sshd
# MAGIC
# MAGIC echo "=== Step 3.2: Replace /etc/ssh/sshd_config ==="
# MAGIC cat > /etc/ssh/sshd_config <<'EOF'
# MAGIC # Supported HostKey algorithms by order of preference.
# MAGIC HostKey /etc/ssh/ssh_host_ed25519_key
# MAGIC HostKey /etc/ssh/ssh_host_rsa_key
# MAGIC HostKey /etc/ssh/ssh_host_ecdsa_key
# MAGIC
# MAGIC KexAlgorithms curve25519-sha256@libssh.org,ecdh-sha2-nistp521,ecdh-sha2-nistp384,ecdh-sha2-nistp256,diffie-hellman-group-exchange-sha256,diffie-hellman-group14-sha1
# MAGIC
# MAGIC Ciphers chacha20-poly1305@openssh.com,aes256-gcm@openssh.com,aes128-gcm@openssh.com,aes256-ctr,aes192-ctr,aes128-ctr
# MAGIC
# MAGIC MACs hmac-sha2-512-etm@openssh.com,hmac-sha2-256-etm@openssh.com,umac-128-etm@openssh.com,hmac-sha2-512,hmac-sha2-256,umac-128@openssh.com
# MAGIC
# MAGIC # Password based logins are disabled - only public key based logins are allowed.
# MAGIC AuthenticationMethods publickey
# MAGIC
# MAGIC # LogLevel VERBOSE logs user's key fingerprint on login. Needed to have a clear audit track of which key was using to log in.
# MAGIC LogLevel VERBOSE
# MAGIC
# MAGIC # Log sftp level file access (read/write/etc.) that would not be easily logged otherwise.
# MAGIC Subsystem sftp internal-sftp
# MAGIC
# MAGIC # Root login is not allowed for auditing reasons. This is because it's difficult to track which process belongs to which root user:
# MAGIC #
# MAGIC # On Linux, user sessions are tracking using a kernel-side session id, however, this session id is not recorded by OpenSSH.
# MAGIC # Additionally, only tools such as systemd and auditd record the process session id.
# MAGIC # On other OSes, the user session id is not necessarily recorded at all kernel-side.
# MAGIC # Using regular users in combination with /bin/su or /usr/bin/sudo ensure a clear audit track.
# MAGIC # PermitRootLogin No
# MAGIC
# MAGIC # We permit root login using keys because root is equivalent to ubuntu inside the container.
# MAGIC PermitRootLogin without-password
# MAGIC
# MAGIC # Use kernel sandbox mechanisms where possible in unprivilegied processes
# MAGIC # Systrace on OpenBSD, Seccomp on Linux, seatbelt on MacOSX/Darwin, rlimit elsewhere.
# MAGIC UsePrivilegeSeparation sandbox
# MAGIC
# MAGIC # UsePAM yes so that disabled accounts (i.e. ubuntu) can login
# MAGIC UsePAM yes
# MAGIC EOF
# MAGIC
# MAGIC echo "=== Step 3.3: Validate and restart system-managed SSH ==="
# MAGIC /usr/sbin/sshd -t
# MAGIC systemctl restart ssh.socket
# MAGIC systemctl restart ssh.service || true
# MAGIC
# MAGIC echo "=== Step 3.4: Verify SSH listener ==="
# MAGIC systemctl status ssh.socket --no-pager || true
# MAGIC systemctl status ssh.service --no-pager || true
# MAGIC ss -tuln | grep ':22' || true
# MAGIC
# MAGIC echo "=== SFTP setup is done. ==="
