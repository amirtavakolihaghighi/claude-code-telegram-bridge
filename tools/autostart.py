r"""Keep the bridge running without a terminal window open.

    python -m tools.autostart install     start it at every sign-in, and now
    python -m tools.autostart status      is it running? is it keeping up?
    python -m tools.autostart start       start it now (installing it if needed)
    python -m tools.autostart stop        stop it now
    python -m tools.autostart uninstall   remove it
    python -m tools.autostart catch-up    send everything never sent, in order

This registers a Windows Scheduled Task that runs at sign-in and keeps going on
battery - which matters on a laptop.

It runs as you, not as the system, because Claude Code uses your own login.
No administrator rights are needed.

Task Scheduler's "restart on failure" only covers a task that fails to
launch, not a program that starts and then fails, so the bridge looks after
itself: it waits for Telegram when the network is not up yet, and starts again
after an unexpected error.
"""
from __future__ import annotations

import getpass
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TASK_NAME = "ClaudeCodeTelegramBridge"
LOG_FILE = ROOT / "runtime" / "bridge.log"
# The bridge notes it is alive every 30 seconds; this long without a note
# means it is stuck, or still waiting for Telegram.
STALLED_AFTER = 300.0
# A live chat always has a little in flight - a tool call waiting for its
# result holds its position back - so this much counts as up to date.
IN_FLIGHT = 256_000

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


def _installed() -> bool:
    return _run(["schtasks", "/Query", "/TN", TASK_NAME])[0] == 0


def _amount(size: int) -> str:
    return f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{size // 1000 or 1} KB"


def _ago(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes ago"
    if seconds < 172800:
        return f"{seconds / 3600:.0f} hours ago"
    return f"{seconds / 86400:.0f} days ago"


def _state():
    """The bridge's settings and state.db, or (None, None) without them."""
    from bridge.config import load_config
    from bridge.state import State
    try:
        config = load_config(require_token=False)
    except SystemExit:
        return None, None
    if not config.state_db.exists():
        return config, None
    return config, State(config.state_db)


def _unsent(state) -> tuple[int, int]:
    """Chats with written messages not yet posted, and how many bytes. A muted
    chat is waiting on purpose, so it is left out."""
    chats = total = 0
    muted = state.muted_chats()
    for session_id, path, offset, _ in state.all_files():
        if session_id in muted:
            continue
        try:
            left = os.path.getsize(path) - offset
        except OSError:
            continue
        if left > 0:
            chats += 1
            total += left
    return chats, total


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
    if code == 0:
        print("It will no longer start at sign-in. Start (option 2) installs "
              "it again.")
    return code


def start() -> int:
    from bridge.instance import running
    if not _installed():
        # Starting in the background is what the task is for, so a missing
        # task is created rather than reported as an error.
        print("Not installed yet - installing it first, so it also starts by "
              "itself at every sign-in. (Option 6 undoes that.)")
        return install()
    up, pid = running()
    if up:
        print(f"Already running (process {pid})." if pid else "Already running.")
        return 0
    code, output = _run(["schtasks", "/Run", "/TN", TASK_NAME])
    print(output or "Started.")
    return code


def stop() -> int:
    from bridge.instance import running
    code, output = _run(["schtasks", "/End", "/TN", TASK_NAME])
    print(output or "Stopped.")
    # The lock is let go only once the process has actually gone.
    for _ in range(20):
        up, pid = running()
        if not up:
            return code
        time.sleep(0.25)
    print(f"A copy is still running (process {pid}) - most likely in a window "
          "of its own ('Run here'). Press Ctrl+C in that window to stop it.")
    return 1


def status() -> int:
    from bridge.catchup import describe, find_missing
    from bridge.instance import running
    code, output = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"])
    if code != 0:
        print("Task:         not installed (option 5 makes it start at sign-in)")
    else:
        for line in output.splitlines():
            if line.split(":")[0].strip() in ("Status", "Last Run Time",
                                              "Last Result"):
                print(line.strip())
    up, pid = running()
    print(f"Running:      {'yes, process ' + str(pid) if up else 'no'}")

    config, state = _state()
    if state is not None:
        alive = state.last_alive()
        if alive is not None:
            print(f"Last active:  {_ago(max(time.time() - alive, 0))}")
        chats, left = _unsent(state)
        print("Sending:      " + (f"{chats} chat(s), {_amount(left)} of chat "
                                  "files still to post" if chats else "up to date"))
        muted = state.muted_chats()
        if muted:
            print(f"Muted:        {len(muted)} chat(s), held until /unmute")
        missing = find_missing(config, state)
        if missing:
            print(f"Never sent:   {describe(missing)}")
            print("              Option 10 sends it.")
        state.close()

    if LOG_FILE.exists():
        print(f"\nLast lines of {LOG_FILE}:")
        tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in tail[-8:]:
            print("   " + line)
    return 0


def brief() -> int:
    """One line for the top of the menu."""
    from bridge.instance import running
    up, _ = running()
    installed = _installed()
    _, state = _state()
    alive = state.last_alive() if state is not None else None
    left = _unsent(state) if state is not None else (0, 0)
    if state is not None:
        state.close()

    if not up:
        print("Installed but not running  -  choose 2 to start" if installed
              else "Not running, not installed  -  choose 5 to have it start "
                   "by itself")
        return 0
    where = "" if installed else " (in a window of its own)"
    if alive is not None and time.time() - alive > STALLED_AFTER:
        print(f"Running{where}, but not working since {_ago(time.time() - alive)}"
              "  -  is Telegram reachable? (7 shows the log)")
    elif left[1] > IN_FLIGHT:
        print(f"Running{where}  -  sending, {_amount(left[1])} to go")
    else:
        print(f"Running{where}  -  up to date")
    return 0


def is_running() -> int:
    """Exit code 0 if a copy of the bridge is running, for bridge.bat."""
    from bridge.instance import running
    up, pid = running()
    if up:
        print(f"The bridge is already running (process {pid})." if pid
              else "The bridge is already running.")
    return 0 if up else 1


def catch_up() -> int:
    """Have the bridge send everything that never reached Telegram."""
    from bridge.catchup import describe, find_missing
    from bridge.instance import running
    config, state = _state()
    if config is None:
        print("There is no .env yet. Copy .env.example to .env and fill it in.")
        return 1
    if state is None:
        # Nothing has been followed on this machine yet, so the first start
        # can simply send everything.
        from bridge.state import State
        config.state_db.parent.mkdir(parents=True, exist_ok=True)
        state = State(config.state_db)
    missing = find_missing(config, state)
    print("To send: " + describe(missing))
    if not missing:
        state.close()
        return 0
    state.request_catch_up()
    state.close()
    print("\nIt goes out oldest message first, in the background, while new "
          "messages carry on as usual. Telegram allows about 20 posts a "
          "minute, so a lot of history takes hours; option 1 shows how far it "
          "has got. Stopping part-way is fine - it carries on from there.")
    up, _ = running()
    if up and _installed():
        print("\nRestarting the bridge so it starts on that now...")
        if stop() != 0:
            return 1
        return start()
    if up:
        print("\nThe bridge is running in a window of its own. Stop it there "
              "(Ctrl+C) and start it again to begin.")
    else:
        print("\nIt begins the next time the bridge starts (option 2, or 9 to "
              "watch it).")
    return 0


COMMANDS = {"install": install, "uninstall": uninstall, "start": start,
            "stop": stop, "status": status, "brief": brief,
            "running": is_running, "catch-up": catch_up}


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
