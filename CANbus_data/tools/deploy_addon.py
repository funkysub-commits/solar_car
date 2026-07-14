"""Deploy the solar-car-canbus LOCAL add-on to HAOS /addons and rebuild it.

The Advanced SSH add-on exposes no SFTP subsystem and the login user can't
write /addons directly, so this streams a gzip tar over the exec channel and
extracts it with sudo. See docs/DEPLOYING_ADDONS.md for the full story.

Traps this script handles (learned the hard way):
  - the SSH add-on disables password auth whenever an authorized key is set,
    so key auth (~/.ssh/id_ed25519) is tried first, password as fallback
  - repo files are CRLF on disk (core.autocrlf=true); a CRLF run.sh fails
    with "bad interpreter" -> text files are normalized to LF in the tarball
  - backups must live in /addons/.backups/, NOT directly under /addons:
    the store scan resolves same-slug folders in arbitrary order and a stale
    backup can shadow the new version ("No update available")
  - `ha` over a non-interactive exec channel lacks SUPERVISOR_TOKEN; every
    ha command is wrapped in `bash -lc` to load the container env
  - rebuild does `--pull` from ghcr.io; without internet it fails AND kills
    the running image -> connectivity is checked first and the deploy aborts

Usage:
    python CANbus_data/tools/deploy_addon.py [pi_ip] [--addon canbus|eink] [--skip-net-check]
    (pi_ip defaults to the host in status.json's Pi.IP; --addon defaults to canbus)
"""
import io, json, os, re, sys, tarfile, time
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ADDONS = {
    "canbus": {
        "local": os.path.join(ROOT, "CANbus_data", "ha_addons", "solar-car-canbus"),
        "remote": "/addons/solar-car-canbus",
        "slug": "local_solarcar_canbus",
    },
    "eink": {
        "local": os.path.join(ROOT, "display", "addon"),
        "remote": "/addons/solar-epaper",
        "slug": "local_solar_epaper",
    },
}
_which = "canbus"
if "--addon" in sys.argv:
    _which = sys.argv[sys.argv.index("--addon") + 1]
LOCAL = ADDONS[_which]["local"]
REMOTE = ADDONS[_which]["remote"]
BACKUPS = "/addons/.backups"
SLUG = ADDONS[_which]["slug"]
EXCLUDE_DIRS = {"__pycache__", ".git"}
TEXT_EXT = {".sh", ".py", ".yaml", ".yml", ".md", ".txt", ".json", ".cfg", ".ini"}
TEXT_NAMES = {"Dockerfile"}
KEY_PATH = os.path.expanduser("~/.ssh/id_ed25519")

with open(os.path.join(ROOT, "status.json")) as f:
    _status = json.load(f)
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
if _which in _args:                    # drop --addon's value from positionals
    _args.remove(_which)
HOST = _args[0] if _args else re.sub(r"^https?://|:\d+$", "", _status["Pi"]["IP"])
SKIP_NET = "--skip-net-check" in sys.argv
PWD = _status["SSH"]["Password"]


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if os.path.exists(KEY_PATH):
        try:
            cli.connect(HOST, username="hassio", key_filename=KEY_PATH,
                        look_for_keys=False, allow_agent=False, timeout=15)
            print("auth: publickey")
            return cli
        except paramiko.AuthenticationException as e:
            print(f"key auth failed ({e}); trying password")
    cli.connect(HOST, username="hassio", password=PWD,
                look_for_keys=False, allow_agent=False, timeout=15)
    print("auth: password")
    return cli


def is_text(name):
    return name in TEXT_NAMES or os.path.splitext(name)[1].lower() in TEXT_EXT


def build_tar():
    buf = io.BytesIO()
    n_norm = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, fs in os.walk(LOCAL):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in sorted(fs):
                if f.endswith(".pyc"):
                    continue
                full = os.path.join(root, f)
                rel = os.path.relpath(full, LOCAL).replace("\\", "/")
                data = open(full, "rb").read()
                if is_text(f) and b"\r\n" in data:
                    data = data.replace(b"\r\n", b"\n")
                    n_norm += 1
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = int(time.time())
                info.mode = 0o755 if f.endswith(".sh") else 0o644
                tar.addfile(info, io.BytesIO(data))
    print(f"tarball: {buf.tell()} bytes, {n_norm} files CRLF->LF normalized")
    return buf.getvalue()


def main():
    cli = connect()

    def run(cmd, stdin_bytes=None, timeout=900):
        si, so, se = cli.exec_command(cmd, timeout=timeout)
        if stdin_bytes is not None:
            si.write(stdin_bytes)
            si.flush()
            si.channel.shutdown_write()
        out = so.read().decode("utf-8", "replace")
        err = se.read().decode("utf-8", "replace")
        return so.channel.recv_exit_status(), out, err

    def run_ha(cmd, timeout=900):
        # ha needs SUPERVISOR_TOKEN, which only login shells load
        return run(f"bash -lc '{cmd}'", timeout=timeout)

    rc, out, err = run_ha(f'ha addons info {SLUG} 2>/dev/null | grep -E "^(version|state):"')
    print(f"--- installed before deploy ---\n{out.strip() or '(not installed)'}")

    if not SKIP_NET:
        rc, out, err = run("nc -z -w 5 ghcr.io 443 && echo INTERNET_OK || echo NO_INTERNET")
        print("net check:", out.strip())
        if "INTERNET_OK" not in out:
            print("ABORT: Pi cannot reach ghcr.io - rebuilding now would kill the "
                  "running image. Get the Pi onto a network with internet first "
                  "(or rerun with --skip-net-check if you know better).")
            cli.close()
            sys.exit(2)

    name = os.path.basename(REMOTE)
    rc, out, err = run(
        f"sudo mkdir -p {BACKUPS} && test -d {REMOTE} "
        f"&& sudo cp -a {REMOTE} {BACKUPS}/{name}.bak.$(date +%Y%m%d-%H%M%S) "
        f"&& ls -d {BACKUPS}/{name}.bak.* | tail -1 || echo no-existing-folder")
    print("backup:", out.strip(), err.strip())

    data = build_tar()
    rc, out, err = run(
        f"sudo rm -rf {REMOTE} && sudo mkdir -p {REMOTE} && sudo tee {REMOTE}/.d.tgz > /dev/null",
        stdin_bytes=data)
    print("upload rc=", rc, err.strip())
    if rc != 0:
        cli.close()
        sys.exit(1)
    rc, out, err = run(
        f"sudo tar xzf {REMOTE}/.d.tgz -C {REMOTE} && sudo rm {REMOTE}/.d.tgz "
        f"&& find {REMOTE} -type f | sort")
    print(f"extract rc={rc}\n{out}{err.strip()}")
    if rc != 0:
        cli.close()
        sys.exit(1)

    rc, out, err = run_ha("ha store reload >/dev/null 2>&1; echo RELOAD_OK")
    print("store reload:", out.strip())
    time.sleep(3)
    rc, out, err = run_ha(f'ha addons info {SLUG} 2>/dev/null | grep -E "^version"')
    if "version:" in out:
        print(f"updating {SLUG} (rebuild, takes a few minutes)...")
        rc, out, err = run_ha(f"ha addons update {SLUG}")
    else:
        print(f"installing {SLUG} (build, takes a few minutes)...")
        rc, out, err = run_ha(f"ha addons install {SLUG}")
    print(f"build rc={rc}\n{out.strip()}\n{err.strip()}")
    if rc != 0:
        cli.close()
        sys.exit(1)

    run_ha(f"ha addons start {SLUG} 2>/dev/null", timeout=120)
    time.sleep(5)
    rc, out, err = run_ha(f'ha addons info {SLUG} | grep -E "^(version|state|boot|protected|watchdog):"')
    print(f"--- after deploy ---\n{out.strip()}")
    rc, out, err = run_ha(f"ha addons logs {SLUG} 2>&1 | tail -40", timeout=120)
    print(f"--- logs (tail) ---\n{out.strip()}\n{err.strip()}")
    cli.close()
    print("DONE - read the logs above; started != working")


if __name__ == "__main__":
    main()
