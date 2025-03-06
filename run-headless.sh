#!/bin/bash

# Make sure the data directory exists
mkdir -p ./data

# Run the headless version
docker-compose --profile headless up -d twitch-watcher-headless

echo "TwitchWatcher headless container is now running."
echo "To view logs: docker-compose logs -f twitch-watcher-headless"
echo "To stop: docker-compose down"