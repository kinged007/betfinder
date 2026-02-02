# Sports Bet Finder

A sports bet finder and placement tracking app.

## Installation

### Option 1: Using the executable file for your OS.

This will preapre the settings file for you and launch the server. It will remain running in your taskbar until you choose to shut it down.

### Option 2: Launch via terminal

1. Ensure you have [uv](https://github.com/astral-sh/uv) installed.
2. Install dependencies:
   
   ```bash
   uv sync
   ```

3. Configure your environment:
   Rename the `sample.env` to `.env` and insert your configurations.

   ```bash
   cp sample.env .env
   ```

4. Perform Database Migrations and run app:
```bash
uv run alembic upgrade head
uv run prod
```

### Option 3: Docker Compose

The docker compose file includes a database container for hosting on a server.

```bash
docker compose up -d
```

## Development

Clone this repo, install as above and run the dev server

```bash
uv run dev
```

- **Dashboard**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
