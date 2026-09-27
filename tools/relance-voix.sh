#!/bin/sh
# Relance le service vocal de Character Factory sur la machine de la voix
# (DGX1) : tue l'ancien, relance `python -m factory.voice_server` depuis ce
# dépôt, journal dans voix.log. Arguments passés au service (--froid, --factice…).
# Le python : FACTORY_VOICE_PYTHON, sinon ~/cf-voice-env/bin/python.
cd "$(dirname "$0")/.." || exit 1
PY="${FACTORY_VOICE_PYTHON:-$HOME/cf-voice-env/bin/python}"
pkill -f "python -m factory.voice_server"
sleep 2
PYTHONUNBUFFERED=1 setsid nohup "$PY" -m factory.voice_server "$@" > voix.log 2>&1 < /dev/null &
echo "service vocal relancé (journal : $(pwd)/voix.log)"
