"""Deploy the solar-car-canbus LOCAL add-on to HAOS /addons.

The Advanced SSH add-on exposes no SFTP subsystem and the login user can't
write /addons directly, so this streams a gzip tar over the exec channel and
extracts it with sudo. After deploy, on HAOS:
    ha addons reload
    ha addons install local_solarcar_canbus
    ha addons start  local_solarcar_canbus

Env:
    HA_HOST   HAOS IP (the SSH add-on, user 'hassio')
    HA_PWD    SSH add-on password
Usage:
    HA_HOST=192.168.1.139 python tools/deploy_addon.py
"""
import os, io, tarfile, paramiko

HOST = os.environ["HA_HOST"]; USER = "hassio"; PWD = os.environ["HA_PWD"]
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.path.join(HERE, "ha_addons", "solar-car-canbus")
REMOTE = "/addons/solar-car-canbus"
EXCLUDE = {"__pycache__", ".git"}

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    for root, dirs, fs in os.walk(LOCAL):
        dirs[:] = [d for d in dirs if d not in EXCLUDE]
        for f in fs:
            if f.endswith(".pyc"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, LOCAL).replace("\\", "/")
            tar.add(full, arcname=rel)
data = buf.getvalue()
print(f"tarball: {len(data)} bytes")

cli = paramiko.SSHClient(); cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, port=22, username=USER, password=PWD,
            look_for_keys=False, allow_agent=False, timeout=15)

def run(cmd, stdin_bytes=None):
    si, so, se = cli.exec_command(cmd, timeout=40)
    if stdin_bytes is not None:
        si.write(stdin_bytes); si.flush(); si.channel.shutdown_write()
    out = so.read().decode("utf-8", "replace"); err = se.read().decode("utf-8", "replace")
    return so.channel.recv_exit_status(), out, err

rc, out, err = run(f"sudo rm -rf {REMOTE}; sudo mkdir -p {REMOTE} && sudo tee {REMOTE}/.d.tgz > /dev/null", stdin_bytes=data)
print("upload rc=", rc, err.strip())
rc, out, err = run(f"sudo tar xzf {REMOTE}/.d.tgz -C {REMOTE} && sudo rm {REMOTE}/.d.tgz && echo ===FILES=== && find {REMOTE} -type f | sort")
print(out); print("extract rc=", rc, err.strip())
cli.close()
