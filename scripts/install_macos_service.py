#!/usr/bin/env python3
import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path


LABEL = "io.github.liusihang.fc27d"


def run(*args, check=True):
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {result.stderr.strip()}")
    return result


def install(proxy_url=None):
    project_root = Path(__file__).resolve().parents[1]
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    logs = Path.home() / "Library" / "Logs"
    launch_agents.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    plist_path = launch_agents / f"{LABEL}.plist"
    environment = {"FC27D_HOST": "127.0.0.1", "FC27D_PORT": "3926"}
    if proxy_url:
        environment.update({"HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url})
    payload = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(project_root / "fc27d.py")],
        "WorkingDirectory": str(project_root),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "ThrottleInterval": 10,
        "StandardOutPath": str(logs / "fc27d.log"),
        "StandardErrorPath": str(logs / "fc27d.log"),
        "EnvironmentVariables": environment,
    }
    domain = f"gui/{os.getuid()}"
    run("launchctl", "bootout", domain, str(plist_path), check=False)
    temporary = plist_path.with_suffix(".plist.new")
    with temporary.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=True)
    os.replace(temporary, plist_path)
    run("launchctl", "bootstrap", domain, str(plist_path))
    run("launchctl", "enable", f"{domain}/{LABEL}")
    run("launchctl", "kickstart", "-k", f"{domain}/{LABEL}")
    return plist_path


def main():
    parser = argparse.ArgumentParser(description="Install the local fc27d macOS LaunchAgent.")
    parser.add_argument("--proxy", help="Optional HTTP/HTTPS proxy URL for FUT.GG refreshes")
    args = parser.parse_args()
    print(install(args.proxy))


if __name__ == "__main__":
    main()
