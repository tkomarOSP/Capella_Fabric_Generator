"""lesson-0005: capella-mcp inherits /etc/cartenza.env, and nothing here names a host.

This unit was the one service outside the /etc/cartenza.env consolidation
(note-0090), so when the dev droplet was promoted to production it kept a stale
KP_CONNECT_BASE_URL and a production session handed a user a connect link on
dev.connect.cartenza.ai.

Two properties are pinned, and the second is the one most at risk from the fix:

  1. The shared file is read, and read BEFORE the private one, so fleet values
     arrive and capella can still override them.
  2. BOTH files stay optional (the leading `-`). Dropping either breaks the
     standalone, non-Cartenza droplet, where neither file exists -- systemd
     treats a missing non-optional EnvironmentFile as a startup failure. That
     deployment has no test that would notice, which is why this asserts it.

Third property: no hostname is hardcoded anywhere in the unit or in the deploy
doc's env-file recipe. The recipe naming `dev.connect.cartenza.ai` as a
production value is what would otherwise recreate the drift on the next box.
"""
import re
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
UNIT = DEPLOY / "capella-mcp.service"
README = DEPLOY / "README.md"

unit = UNIT.read_text(encoding="utf-8")

# Directives only -- the comments above them legitimately discuss the history,
# including the dev hostname, so a naive substring search over the whole file
# would pass or fail for the wrong reason.
directives = [ln.strip() for ln in unit.splitlines() if not ln.strip().startswith("#")]
env_files = [ln for ln in directives if ln.startswith("EnvironmentFile=")]

assert len(env_files) == 2, f"expected exactly two EnvironmentFile lines, got {env_files}"

shared, private = env_files
assert shared == "EnvironmentFile=-/etc/cartenza.env", shared
assert private == "EnvironmentFile=-/etc/capella-mcp.env", private
print("unit: reads the shared fleet env file before its own")

# The standalone property. Spelled out per-file so a failure names which one.
for line in env_files:
    path = line.split("=", 1)[1]
    assert path.startswith("-"), (
        f"{line} is not optional -- a droplet without this file will fail to "
        "start, which breaks the standalone deployment"
    )
print("unit: both env files are optional, so a bare droplet still starts")

# No host may be baked into the unit: unit files are copied from git on every
# deploy, so a real value in one is silently replaced by the repo's placeholder.
for line in directives:
    if line.startswith("Environment="):
        assert "cartenza.ai" not in line, (
            f"unit hardcodes a hostname: {line} -- host values belong in "
            "/etc/cartenza.env, which a deploy never touches"
        )
print("unit: no hostname hardcoded in an Environment= directive")

# The deploy doc must not put a fleet-wide host back into the private env file.
# Scope to the bash block that writes /etc/capella-mcp.env; prose elsewhere
# legitimately mentions the variable by name when explaining the inheritance.
readme = README.read_text(encoding="utf-8")
recipe = re.search(
    r"cat > /etc/capella-mcp\.env <<'EOF'\n(.*?)\nEOF", readme, re.DOTALL
)
assert recipe, "could not find the /etc/capella-mcp.env recipe in deploy/README.md"
body = recipe.group(1)
for leaked in ("KP_CONNECT_BASE_URL", "KP_VIEWER_BASE_URL",
               "CARTENZA_CONNECT_SECRET_KEY", "CARTENZA_DB_PATH"):
    assert leaked not in body, (
        f"deploy/README.md tells the operator to set {leaked} in the private "
        "env file -- that is the per-service copy that drifted; it is "
        "inherited from /etc/cartenza.env now"
    )
assert "cartenza.ai" not in body, (
    f"the env-file recipe names a hostname:\n{body}\n"
    "Naming an environment here is what recreated the dev value on a "
    "production box."
)
print("deploy doc: the private env-file recipe names no fleet value and no host")

print("\nALL LESSON-0005 CHECKS PASSED")
