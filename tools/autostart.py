r"""Keep the bridge running without a terminal window open.

    python -m tools.autostart install     start it at every sign-in
    python -m tools.autostart status      is it installed? is it running?
    python -m tools.autostart start       start it now
    python -m tools.autostart stop        stop it now
    python -m tools.autostart uninstall   remove it

This registers a Windows Scheduled Task that runs at sign-in, restarts itself
if it ever crashes, and keeps going on battery - which matters on a laptop.

It runs as you, not as the system, because Claude Code uses your own login.
No administrator rights are needed.
"""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_NAME = "ClaudeCodeTelegramBridge"
LOG_FILE = ROOT / "runtime" / "bridge.log"

# pythonw.exe runs without opening a console window.
PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def _xml(user: str) -> str:
    arguments = f'-m bridge --log-file "{LOG_FILE}"'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Mirrors Claude Code chats to Telegram and runs replies.</Description>
    <URI>\\{TASK_NAME}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>99</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{PYTHONW}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{ROOT}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _run(args: list[str]) -> tuple[int, str]:
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, (result.stdout + result.stderr).strip()


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME") or getpass.getuser()
    return f"{domain}\\{user}" if domain else user


def install() -> int:
    if sys.platform != "win32":
        print("This installer is for Windows. On Linux or macOS use a systemd "
              "user service or a launchd agent instead.")
        return 1
    if not PYTHONW.exists():
        print(f"Could not find {PYTHONW}. Create the environment first:")
        print(r"   python -m venv .venv")
        print(r"   .venv\Scripts\python -m pip install -r requirements.txt")
        return 1
    if not (ROOT / ".env").exists():
        print("There is no .env yet. Copy .env.example to .env and fill it in "
              "before installing.")
        return 1

    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    # schtasks insists on UTF-16 for XML it imports.
    handle, temp_path = tempfile.mkstemp(suffix=".xml")
    os.close(handle)
    Path(temp_path).write_text(_xml(_current_user()), encoding="utf-16")

    try:
        code, output = _run(["schtasks", "/Create", "/TN", TASK_NAME,
                             "/XML", temp_path, "/F"])
    finally:
        Path(temp_path).unlink(missing_ok=True)

    if code != 0:
        print("Could not create the task:")
        print(output)
        return code

    print(f"Installed. '{TASK_NAME}' will start every time you sign in.")
    print(f"Log file: {LOG_FILE}")
    print("Starting it now...")
    return start()


def uninstall() -> int:
    code, output = _run(["schtasks", "/End", "/TN", TASK_NAME])
    code, output = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    print(output or ("Removed." if code == 0 else "Nothing to remove."))
    return code


def start() -> int:
    code, output = _run(["schtasks", "/Run", "/TN", TASK_NAME])
    print(output or "Started.")
    return code


def stop() -> int:
    code, output = _run(["schtasks", "/End", "/TN", TASK_NAME])
    print(output or "Stopped.")
    return code


def status() -> int:
    code, output = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    if code != 0:
        print("Not installed. Run:  python -m tools.autostart install")
        return code
    for line in output.splitlines():
        if line.split(":")[0].strip() in ("TaskName", "Status", "Last Run Time",
                                          "Last Result", "Next Run Time"):
            print(line.strip())
    if LOG_FILE.exists():
        print(f"\nLast lines of {LOG_FILE}:")
        tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in tail[-8:]:
            print("   " + line)
    return 0


COMMANDS = {"install": install, "uninstall": uninstall, "start": start,
            "stop": stop, "status": status}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    if action not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[action]()


if __name__ == "__main__":
    raise SystemExit(main())
