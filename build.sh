#!/usr/bin/env bash
# Exit on error
set -o errexit

# Modify this line as needed for your package manager (pip, poetry, etc.)
pip install -r requirements-render.txt

# Convert static asset files
python manage.py collectstatic --no-input

# Apply any outstanding database migrations
python manage.py migrate

# Create superuser from environment variables (optional, only if SUPERUSER_USERNAME is set)
if [ -n "$SUPERUSER_USERNAME" ]; then
  python manage.py create_superuser_env
fi
