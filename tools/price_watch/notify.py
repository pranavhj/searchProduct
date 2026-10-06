"""Deliver alerts: Discord (existing openclaw discord-send.py) and Windows toast."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("price_watch.notify")

DISCORD_SEND = Path(r"D:\MyData\Software\openclaw-config\bin\discord-send.py")

# PowerShell's own AppUserModelID: lets an unpackaged script raise a toast without extra modules.
_TOAST_PS = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:PW_TOAST_TITLE)) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:PW_TOAST_BODY)) > $null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""


def send_discord(target: str, message: str) -> bool:
    if not DISCORD_SEND.exists():
        log.error("discord-send.py not found at %s", DISCORD_SEND)
        return False
    try:
        proc = subprocess.run(
            [sys.executable, str(DISCORD_SEND), "--target", target, "--message", message],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("discord send failed: %s", exc)
        return False
    if proc.returncode != 0:
        log.error("discord send rc=%d stderr=%s", proc.returncode, proc.stderr.strip()[:500])
        return False
    log.info("discord message sent to %s (%d chars)", target, len(message))
    return True


def send_toast(title: str, body: str) -> bool:
    if os.name != "nt":
        return False
    env = {**os.environ, "PW_TOAST_TITLE": title[:120], "PW_TOAST_BODY": body[:400]}
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("toast failed: %s", exc)
        return False
    if proc.returncode != 0:
        log.error("toast rc=%d stderr=%s", proc.returncode, proc.stderr.strip()[:500])
        return False
    log.info("toast shown: %s", title)
    return True
