"""Push a local directory to a remote path on the HA host via SSH exec.

Usage:
    set HA_PWD=...
    python ha_push.py <local_dir> <remote_dir>

The SSH add-on doesn't expose the SFTP subsystem, so we stream file contents
to `cat > REMOTE_PATH` via exec_command instead. Line endings normalized to
LF for text files. .sh files are chmod 0755 after upload.
"""
import os
import posixpath
import shlex
import sys

import paramiko

HOST = os.environ.get("HA_HOST", "10.126.155.163")
PORT = 22
USER = "hassio"
TEXT_EXTS = {".sh", ".yaml", ".yml", ".py", ".txt", ".md"}
TEXT_NAMES = {"Dockerfile"}

if len(sys.argv) != 3:
    print("usage: python ha_push.py <local_dir> <remote_dir>", file=sys.stderr)
    sys.exit(2)

local_dir = sys.argv[1]
remote_dir = sys.argv[2].replace("\\", "/")

pwd = os.environ.get("HA_PWD")
if not pwd:
    print("HA_PWD not set", file=sys.stderr)
    sys.exit(2)

if not os.path.isdir(local_dir):
    print(f"not a directory: {local_dir}", file=sys.stderr)
    sys.exit(2)


def run(client, cmd, stdin_bytes=None):
    """Run a shell command on the remote, optionally piping stdin_bytes. Returns (rc, out, err)."""
    stdin, stdout, stderr = client.exec_command(cmd)
    if stdin_bytes is not None:
        stdin.write(stdin_bytes)
        stdin.channel.shutdown_write()
    out = stdout.read()
    err = stderr.read()
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(HOST, port=PORT, username=USER, password=pwd,
               look_for_keys=False, allow_agent=False, timeout=10)

# Detect whether we need sudo (we will if the parent dir isn't writable)
parent = posixpath.dirname(remote_dir.rstrip("/")) or "/"
rc, out, _ = run(client, f"test -w {shlex.quote(parent)} && echo yes || echo no")
need_sudo = out.strip() == b"no"
if need_sudo:
    rc, _, err = run(client, "sudo -n true")
    if rc != 0:
        print(f"need sudo for {parent} but sudo unavailable: {err.decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
    print(f"using sudo (parent {parent} not writable by this user)")
SUDO = "sudo " if need_sudo else ""

# mkdir -p
rc, _, err = run(client, f"{SUDO}mkdir -p {shlex.quote(remote_dir)}")
if rc != 0:
    print(f"mkdir failed: {err.decode(errors='replace')}", file=sys.stderr)
    sys.exit(1)
print(f"mkdir -p {remote_dir}")

for name in sorted(os.listdir(local_dir)):
    src = os.path.join(local_dir, name)
    if not os.path.isfile(src):
        continue
    dst = posixpath.join(remote_dir, name)
    ext = os.path.splitext(name)[1].lower()
    is_text = (ext in TEXT_EXTS) or (name in TEXT_NAMES)

    with open(src, "rb") as f:
        data = f.read()
    if is_text:
        data = data.replace(b"\r\n", b"\n")

    # When using sudo, `sudo cat > path` only redirects via the calling user's
    # shell -- doesn't escalate. Use `sudo tee` instead (it does the write).
    if need_sudo:
        cmd = f"sudo tee {shlex.quote(dst)} > /dev/null"
    else:
        cmd = f"cat > {shlex.quote(dst)}"
    rc, _, err = run(client, cmd, stdin_bytes=data)
    if rc != 0:
        print(f"upload {name} failed: {err.decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)

    if ext == ".sh":
        run(client, f"{SUDO}chmod 0755 {shlex.quote(dst)}")

    rc, out, _ = run(client, f"wc -c < {shlex.quote(dst)}")
    remote_size = int(out.strip()) if rc == 0 and out.strip() else -1
    tag = " [LF-normalized]" if is_text else ""
    print(f"pushed {name} -> {dst} ({remote_size} B){tag}")

client.close()
