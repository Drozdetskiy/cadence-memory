import os

# Force no-color help/output so Rich does not split flag names across ANSI
# style spans (e.g. `--mode` rendered as `-\x1b[0m\x1b[1;36m-mode`, breaking
# substring assertions in CLI tests).
os.environ.setdefault("NO_COLOR", "1")
