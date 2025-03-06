#!/bin/bash

# Make sure the data directory exists
mkdir -p ./data

# Set display variable if not set
if [ -z "$DISPLAY" ]; then
  export DISPLAY=:0
fi

# Run the docker-compose command
docker-compose up -d

echo "TwitchWatcher container is now running."
echo "To view logs: docker-compose logs -f"
echo "To stop: docker-compose down"