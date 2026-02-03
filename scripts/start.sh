#!/bin/bash
set -e

# Run migrations
echo "Running database migrations..."
pwd
ls -la
if [ -d "alembic" ]; then
    echo "alembic directory exists"
    ls -la alembic
else
    echo "alembic directory NOT found"
fi
alembic upgrade head

# Start application
echo "Starting application on port ${PORT:-8123}..."
# We use "exec" so that uvicorn becomes the main process (PID 1) to handle signals correctly
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8123}"
