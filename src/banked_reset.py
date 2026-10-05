#!/usr/bin/env python3
"""Codex banked-reset client. Read-only unless --apply is explicitly supplied."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid


class RPC:
    def __init__(self, executable):
        self.proc = subprocess.Popen(
            [executable, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self.messages = queue.Queue()
        self.serial = 0
        threading.Thread(target=self._read, daemon=True).start()
        try:
            self.call("initialize", {"clientInfo": {
                "name": "banked_reset_monitor", "version": "0.1.0"}})
            self.send({"method": "initialized", "params": {}})
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.proc.stdout:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue
        self.messages.put(None)

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def call(self, method, params=None, timeout=40):
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + timeout
        while True:
            try:
                msg = self.messages.get(timeout=max(0, deadline-time.monotonic()))
            except queue.Empty:
                raise RuntimeError("RPC timed out; a pending redemption keeps its original retry key.")
            if msg is None:
                raise RuntimeError("App server exited; check the CLI and sign-in.")
            if msg.get("id") != request_id:
                # No model turns are started; ignore unrelated notifications.
                continue
            if "error" in msg:
                raise RuntimeError(f"RPC {method}: {msg['error']}")
            return msg["result"]

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream is not None:
                stream.close()


def date(timestamp):
    from terminal_ui import timestamp as format_timestamp
    return format_timestamp(timestamp)


def notify(message):
    """Best-effort local notification; failure never interrupts redemption."""
    if sys.platform == 'darwin':
        command = ['/usr/bin/osascript', '-e',
                   'on run argv\ndisplay notification (item 1 of argv) with title "refill"\nend run', message]
    elif sys.platform.startswith('linux') and shutil.which('notify-send'):
        command = [shutil.which('notify-send'), '--', 'refill', message]
    else:
        return
    try:
        result = subprocess.run(command, capture_output=True, timeout=10)
        if result.returncode:
            print('Warning: desktop notification failed; check the log.')
    except (OSError, subprocess.TimeoutExpired):
        print('Warning: desktop notification unavailable; check the log.')


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as out:
        json.dump(value, out)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def policy(snapshot, args, now):
    buckets = snapshot.get("rateLimitsByLimitId")
    bucket = buckets.get("codex") if buckets else snapshot.get("rateLimits")
    if not bucket or bucket.get("limitId") not in (None, "codex"):
        return None, "Quota Codex Unknown."
    summary = snapshot.get("rateLimitResetCredits") or {}
    eligible = [c for c in summary.get("credits") or []
                if c.get("status") == "available"
                and c.get("resetType") == "codexRateLimits"
                and (c.get("expiresAt") is None or c["expiresAt"] > now)]
    if not eligible:
        return None, "No eligible reset with sufficient redemption details."
    credit = min(eligible, key=lambda c: c.get("expiresAt") or float("inf"))
    if bucket.get("spendControlReached") or bucket.get("rateLimitReachedType") in (
            "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached",
            "workspace_owner_credits_depleted", "workspace_member_credits_depleted"):
        return None, "Workspace or spending restriction detected; do not redeem a reset."
    if getattr(args, "force", False):
        return credit, "Immediate redemption explicitly requested."
    if not args.demand:
        return None, "No explicit demand for usage (--demand)."
    windows = [bucket[k] for k in ("primary", "secondary") if bucket.get(k)]
    exhausted = (bucket.get("rateLimitReachedType") == "rate_limit_reached"
                 or any(w.get("usedPercent", 0) >= 100 for w in windows))
    if exhausted:
        return credit, "Quota exhausted; redeem an available reset."
    expiry = credit.get("expiresAt")
    if expiry is not None and 0 < expiry - now <= args.wait_minutes * 60:
        return credit, "An available reset expires within the configured threshold; redeem before expiry."
    return None, "Quota remains and no available reset expires within the configured threshold."


def report(snapshot):
    from terminal_ui import section, field, flag, progress, table, timestamp, local_zone
    section('Usage', local_zone().key)
    field('Included access', flag(snapshot.get('ordinaryUsageAllowed'), 'Available', 'Blocked'))
    buckets = snapshot.get('rateLimitsByLimitId') or {'codex': snapshot.get('rateLimits') or {}}
    rows = []
    for name, bucket in buckets.items():
        for key in ('primary', 'secondary'):
            window = bucket.get(key)
            if not window:
                continue
            minutes = window.get('windowDurationMins')
            period = {10080: 'Weekly', 300: '5-hour'}.get(minutes, f'{minutes} min' if minutes else 'Unknown')
            used = window.get('usedPercent')
            remaining = f'{max(0, min(100, 100-used)):g}%' if isinstance(used, (int, float)) else 'Unknown'
            next_reset = timestamp(window.get('resetsAt'), compact=True)
            if minutes == 10080:
                next_reset += ' (weekly)'
            rows.append([name, period, progress(used, width=12), remaining, next_reset])
    if rows:
        table(['Quota', 'Window', 'Used', 'Remaining', 'Next reset'], rows, tones={3: '32'})
    else:
        field('Quota windows', 'Unavailable')


def report_banked(snapshot):
    from terminal_ui import section, field, table, timestamp, local_zone
    summary = snapshot.get('rateLimitResetCredits') or {}
    section('Banked resets', local_zone().key)
    field('Available', summary.get('availableCount', 'Unknown'))
    credits = sorted(summary.get('credits') or [], key=lambda c: c.get('expiresAt') or float('inf'))
    if summary.get('credits') is None:
        field('Details', 'Unavailable')
    if credits:
        table(['#', 'Reset', 'Status', 'Granted', 'Expires'], [
            [str(index), 'Full reset' if credit.get('resetType') == 'codexRateLimits' else 'Reset',
             {'available': 'Available', 'consumed': 'Consumed', 'expired': 'Expired'}.get(credit.get('status'), 'Unknown'),
             timestamp(credit.get('grantedAt'), compact=True), timestamp(credit.get('expiresAt'), compact=True)]
            for index, credit in enumerate(credits, 1)], tones={2: '32'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--banked-only", action="store_true", help="Read the list of banked resets only")
    parser.add_argument("--apply", action="store_true", help="Enable reset redemption")
    parser.add_argument("--force", action="store_true", help="Redeem before the quota is exhausted")
    parser.add_argument("--demand", action="store_true", help="The caller requests Codex usage now")
    parser.add_argument("--expiry-minutes", "--wait-minutes", dest="wait_minutes", type=int, default=None, help="Redeem an available reset this many minutes before its expiry")
    parser.add_argument("--notify", action="store_true", help="Local macOS notifications")
    parser.add_argument("--state-dir", type=Path, default=Path(__file__).resolve().parent / ".reset-state")
    args = parser.parse_args()
    if args.wait_minutes is None:
        from refill_config import read
        args.wait_minutes = read()['wait_minutes']
    if args.wait_minutes < 0:
        parser.error("--wait-minutes must be >= 0")
    os.umask(0o077)
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = args.state_dir / "state.json"
    with (args.state_dir / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another check is already running.")
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        rpc = None
        try:
            rpc = RPC(args.codex)
            snapshot = rpc.call("account/rateLimits/read")
            if args.banked_only:
                report_banked(snapshot)
                return
            report(snapshot)
            account = snapshot.get("accountId")
            if not account:
                raise RuntimeError("Account could not be identified; redemption disabled.")
            if state.get("accountId") not in (None, account):
                raise RuntimeError("Account changed; use a separate state directory.")
            state["accountId"] = account
            now = time.time()
            state["lastCheck"] = now
            state["availableCount"] = (snapshot.get("rateLimitResetCredits") or {}).get("availableCount")
            save(state_path, state)
            for credit in (snapshot.get("rateLimitResetCredits") or {}).get("credits") or []:
                expiry = credit.get("expiresAt")
                if expiry and 0 < expiry - now < 86400:
                    print("Warning: a reset expires within 24 hours; review whether to use it.")
                    notified = state.setdefault("expiryNotified", [])
                    if args.notify and credit["id"] not in notified:
                        notify("A banked reset expires at " + date(expiry))
                        notified.append(credit["id"])
                        save(state_path, state)
            pending = state.get("pending")
            if pending:
                print("Reconciling a previous redemption with the original retry key.")
            else:
                if state.get("halted"):
                    print("Blocked: a previous redemption did not confirm restored access; review manually.")
                    return
                credit, reason = policy(snapshot, args, now)
                state["decision"] = reason
                save(state_path, state)
                print("Decision:", reason)
                if not credit:
                    return
                if not args.force and now - state.get("lastSuccess", 0) < 3600:
                    print("Cooldown: a reset was already redeemed within the last hour.")
                    return
                pending = {"idempotencyKey": str(uuid.uuid4()), "creditId": credit["id"]}
            if not args.apply:
                print("Read-only check: no redemption request sent.")
                if args.notify and state.get("simulationNotified") != pending["creditId"]:
                    notify("A reset would be redeemed. Redemption is disabled.")
                    state["simulationNotified"] = pending["creditId"]
                    save(state_path, state)
                return
            if not state.get("pending"):
                # Revalidate immediately before consuming; never act on the earlier snapshot alone.
                latest = rpc.call("account/rateLimits/read")
                latest_credit, reason = policy(latest, args, time.time())
                if latest.get("accountId") != account or not latest_credit:
                    print("Redemption cancelled after rechecking:", reason)
                    return
                pending["creditId"] = latest_credit["id"]
            state["pending"] = pending
            save(state_path, state)  # Persist BEFORE a potentially irreversible request.
            result = rpc.call("account/rateLimitResetCredit/consume", pending)
            outcome = result.get("outcome")
            print("Redemption result:", outcome)
            if outcome not in ("reset", "alreadyRedeemed", "nothingToReset", "noCredit"):
                raise RuntimeError("Unknown result; the pending retry key was preserved.")
            after = rpc.call("account/rateLimits/read")
            if after.get("accountId") != account:
                raise RuntimeError("Account mismatch during verification.")
            report(after)
            # Service outcome confirms redemption, not necessarily resumed access.
            if outcome in ("reset", "alreadyRedeemed"):
                state["lastSuccess"] = time.time()
                if after.get("ordinaryUsageAllowed") is not True:
                    print("Warning: reset redeemed, but restored access is not yet confirmed.")
                    state["halted"] = True
                if args.notify:
                    notify("Reset redeemed. Included usage allowed: "
                           + str(after.get("ordinaryUsageAllowed")))
            state.pop("pending", None)
            save(state_path, state)
        finally:
            if rpc is not None:
                rpc.close()


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as error:
        print("Error:", error, file=sys.stderr)
        sys.exit(1)
