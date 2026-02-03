#!/bin/bash
set -e

# Run migrations
echo "Running database migrations..."
alembic upgrade head

# Start application
echo "Starting application on port ${PORT:-8123}..."
# We use "exec" so that uvicorn becomes the main process (PID 1) to handle signals correctly
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8123}"
