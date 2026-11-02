"""Launch a Windows child with its own log handles, independent of PowerShell."""
from pathlib import Path
import subprocess
import sys

root, log_dir, name, *command = sys.argv[1:]
with (Path(log_dir) / f"{name}.out.log").open("wb") as stdout, \
     (Path(log_dir) / f"{name}.err.log").open("wb") as stderr:
    process = subprocess.Popen(
        command, cwd=root, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
        close_fds=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
print(process.pid)
