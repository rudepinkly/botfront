#!/bin/bash
# start.sh
set -e
# поднимаем webapp и бота вместе
uvicorn webapp:app --host 0.0.0.0 --port 8000 &
python bot.py
