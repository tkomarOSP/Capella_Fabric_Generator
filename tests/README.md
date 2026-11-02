# Tests

```
python tests/run_all.py
```

Each file is a standalone script, not a pytest case, and `run_all.py` gives each
one its own process.

**Two interpreters, and the tests pick for themselves.** Anything that touches
capellambse re-executes itself under `.venv\Scripts\python.exe` — the
capellambse this service actually deploys, since what the metamodel exposes is a
fact about the installed version rather than about our code. The tests that
exercise the kp-auth wiring deliberately do *not*: that venv has no sqlalchemy,
because `kp-auth` is an optional dependency here and only the Cartenza
deployment installs it. So run the suite with either and each test will land
where it belongs.

Model-backed tests read Trail Power from `Capella_Tools`, and the context-diagram
tests need the ELK layout binary — see `deploy/README.md` §4b. Without it the
first render downloads it (~20s) rather than failing.

**`test_diagram_views.py` takes about two minutes, and that is deliberate.** It
renders every authored diagram in Trail Power — 88 of them — rather than a
representative one. Two shipped defects hid behind a single-diagram test: the
element table listing 54 ports (the one component checked was at the logical
layer, which draws no ports) and 12 diagrams failing to render at all (the one
diagram checked was not a PAB). The sweep renders straight off the already-open
model rather than through `render_diagram_view`, which reloads the model per
call; going through the tool took 12.6 minutes. Narrow it again and you lose
exactly the coverage that caught those two.

These were recovered from a `%TEMP%` scratchpad that Windows was deleting
piecemeal; see `se-knowledge-partner/tests/README.md`.
