# TwitchWatcher Docker Guide

This guide provides instructions for running TwitchWatcher in Docker containers.

## Prerequisites

- Docker (20.10.0 or newer)
- Docker Compose (2.0.0 or newer)
- X server for GUI mode

## Running with GUI

The default configuration runs TwitchWatcher with its GUI enabled. This requires:
- An X server running on the host
- Proper DISPLAY environment variable configuration

### Quick Start (GUI Mode)

1. Run the included script:
   ```bash
   ./run.sh
   ```

2. Or manually:
   ```bash
   # Set display variable if needed
   export DISPLAY=:0
   
   # Start the container
   docker-compose up -d
   ```

3. View the logs:
   ```bash
   docker-compose logs -f
   ```

## Running Headless

For server environments without a display, use the headless mode:

```bash
./run-headless.sh
```

Or manually:
```bash
docker-compose --profile headless up -d twitch-watcher-headless
```

## Data Persistence

All application data is stored in the `./data` directory, which is mounted as a volume in the container.

## Stopping the Container

```bash
docker-compose down
```

## Troubleshooting

1. If you see X11 errors:
   - Ensure X server is running
   - Check DISPLAY environment variable
   - Run: `xhost +local:docker`

2. If the application crashes:
   - Check logs: `docker-compose logs`
   - Ensure proper networking is available

## Building Custom Images

To build a custom image:

```bash
# With GUI
docker build -t twitch-watcher .

# Without GUI
docker build --build-arg WITH_GUI=false -t twitch-watcher-headless .
```

## Security Note

The data directory contains authentication cookies. Keep it secure to prevent unauthorized access to your Twitch account.