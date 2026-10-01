#!/usr/bin/env python3
"""Start a command in its own session (survives parent/task kills). Usage:
   daemonize.py <logfile> <cmd> [args...]     (env vars pass through)
On macOS the command is wrapped in `caffeinate -ims` so the machine does not
idle-sleep mid-run (this laptop sleeps after 1 min idle; a sleeping machine
stalls the real-time AgentSession and corrupts every wall-clock metric)."""
import os, shutil, subprocess, sys
log = open(sys.argv[1], "ab")
cmd = sys.argv[2:]
if sys.platform == "darwin" and shutil.which("caffeinate") and os.environ.get("NO_CAFFEINATE") != "1":
    cmd = ["caffeinate", "-ims"] + cmd
p = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True, cwd=os.environ.get("DAEMON_CWD") or None)
print(p.pid)
