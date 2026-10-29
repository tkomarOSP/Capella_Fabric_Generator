"""Run every test in this directory, one process each, and report pass/fail.

Each test is a standalone script with its own setup (temp databases, real git
repos, seeded rows), not a pytest case -- so they get a process each rather
than a shared interpreter, and one test's environment cannot leak into the
next. The exit code is the number of failures, capped at 125.

These tests were written in a scratchpad under %TEMP% and lived there for
weeks. Windows temp cleanup deletes files that age out, so the suite was
quietly shrinking -- it is in the repo now for that reason.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    tests = sorted(p for p in HERE.glob("test_*.py"))
    failed = []
    for t in tests:
        r = subprocess.run([sys.executable, str(t)], cwd=HERE,
                           capture_output=True, text=True)
        if r.returncode != 0:
            failed.append(t.name)
            print(f"FAIL {t.name}")
            tail = (r.stdout + r.stderr).strip().splitlines()[-12:]
            for line in tail:
                print("     " + line)
    print(f"\npass={len(tests) - len(failed)} fail={len(failed)} of {len(tests)}")
    return min(len(failed), 125)


if __name__ == "__main__":
    raise SystemExit(main())
