import sys

from .cli import main

# Sous Windows, une sortie redirigée prend l'encodage local (cp1252), qui
# n'a ni « → » ni « ° » : on écrit en UTF-8 partout.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

sys.exit(main())
