"""Run a command (or shell prompt) on the Home Assistant SSH add-on.

Usage:
    set HA_PWD=...
    python ha_run.py "hostname"
    python ha_run.py "uname -a"
    python ha_run.py "ls /config"

Reads HA_PWD from environment so the password isn't written to disk.
Hostname/user are constants at the top.
"""
import os
import sys
import paramiko

HOST = "100.100.79.71"
PORT = 22
USER = "hassio"

if len(sys.argv) < 2:
    print("usage: python ha_run.py '<command>'", file=sys.stderr)
    sys.exit(2)

pwd = os.environ.get("HA_PWD")
if not pwd:
    print("HA_PWD env var not set", file=sys.stderr)
    sys.exit(2)

command = sys.argv[1]

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect(HOST, port=PORT, username=USER, password=pwd,
                   look_for_keys=False, allow_agent=False, timeout=10)
except paramiko.AuthenticationException:
    print("auth failed (check HA_PWD)", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"connection failed: {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)

_, stdout, stderr = client.exec_command(command, timeout=30)
out = stdout.read().decode("utf-8", errors="replace")
err = stderr.read().decode("utf-8", errors="replace")
rc = stdout.channel.recv_exit_status()
sys.stdout.write(out)
if err:
    sys.stderr.write(err)
client.close()
sys.exit(rc)
