"""
DMCPatchAgent updater.

A small standalone exe (build separately with --onefile) that lives next to
dmcpatchagent.exe. The agent cannot replace its own running exe, so it:

  1. Downloads the new build to dmcpatchagent_new.exe
  2. Launches THIS updater as a detached process
  3. Exits

This updater then:
  1. Stops the DMCPatchAgent service (via nssm)
  2. Waits for the process to fully release the exe file
  3. Renames dmcpatchagent.exe -> dmcpatchagent_old.exe
  4. Renames dmcpatchagent_new.exe -> dmcpatchagent.exe
  5. Starts the DMCPatchAgent service
  6. Logs everything to updater.log for troubleshooting

Build:
    pyinstaller --onefile updater.py --name updater --noconsole
"""
import os
import sys
import time
import subprocess
import datetime

_exe_dir = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))

SERVICE_NAME = "DMCPatchAgent"
NSSM     = os.path.join(_exe_dir, "nssm.exe")
CUR_EXE  = os.path.join(_exe_dir, "dmcpatchagent.exe")
NEW_EXE  = os.path.join(_exe_dir, "dmcpatchagent_new.exe")
OLD_EXE  = os.path.join(_exe_dir, "dmcpatchagent_old.exe")
LOG_PATH = os.path.join(_exe_dir, "updater.log")

CREATE_NO_WINDOW = 0x08000000


def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(line + "\n")
    except Exception:
        pass


def run(cmd, timeout=30):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=CREATE_NO_WINDOW
        )
        log(f"  cmd: {' '.join(cmd)} -> exit {result.returncode}")
        if result.stdout.strip():
            log(f"  stdout: {result.stdout.strip()[:300]}")
        if result.stderr.strip():
            log(f"  stderr: {result.stderr.strip()[:300]}")
        return result.returncode
    except Exception as e:
        log(f"  ERROR running {cmd}: {e}")
        return -1


def wait_for_unlock(path, timeout=30):
    """Wait until a file is no longer locked (process has exited)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            # Try to open exclusively - if locked, this raises
            with open(path, 'a'):
                pass
            return True
        except (PermissionError, OSError):
            time.sleep(1)
    return False


def main():
    log("=== Updater started ===")

    if not os.path.exists(NEW_EXE):
        log(f"ERROR: {NEW_EXE} not found. Aborting.")
        return

    log(f"Stopping service {SERVICE_NAME}...")
    run([NSSM, "stop", SERVICE_NAME], timeout=60)

    log("Waiting for old exe to be released...")
    time.sleep(2)
    if not wait_for_unlock(CUR_EXE, timeout=30):
        log("WARNING: old exe still locked after timeout, proceeding anyway")

    # Remove previous backup if present
    if os.path.exists(OLD_EXE):
        try:
            os.remove(OLD_EXE)
            log(f"Removed old backup {OLD_EXE}")
        except Exception as e:
            log(f"Could not remove old backup: {e}")

    # Move current exe to backup
    try:
        os.rename(CUR_EXE, OLD_EXE)
        log(f"Renamed {CUR_EXE} -> {OLD_EXE}")
    except Exception as e:
        log(f"ERROR: could not rename current exe: {e}")
        log("Attempting to restart service with existing exe...")
        run([NSSM, "start", SERVICE_NAME], timeout=60)
        return

    # Move new exe into place
    try:
        os.rename(NEW_EXE, CUR_EXE)
        log(f"Renamed {NEW_EXE} -> {CUR_EXE}")
    except Exception as e:
        log(f"ERROR: could not move new exe into place: {e}")
        log("Restoring backup...")
        try:
            os.rename(OLD_EXE, CUR_EXE)
        except Exception as e2:
            log(f"ERROR: could not restore backup either: {e2}")
        run([NSSM, "start", SERVICE_NAME], timeout=60)
        return

    if os.path.exists(OLD_EXE):
        try:
            os.remove(OLD_EXE)
            log(f"Removed old backup {OLD_EXE}")
        except Exception as e:
            log(f"Could not remove old backup: {e}")

    log(f"Starting service {SERVICE_NAME}...")
    run([NSSM, "start", SERVICE_NAME], timeout=60)

    log("=== Updater finished successfully ===")


if __name__ == "__main__":
    main()