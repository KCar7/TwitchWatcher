FROM python:3.10-slim

WORKDIR /app

# Install base dependencies
RUN apt-get update && apt-get install -y \
    libglib2.0-0 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Install GUI-specific dependencies
ARG WITH_GUI=true
RUN if [ "$WITH_GUI" = "true" ]; then \
    apt-get update && apt-get install -y \
    libgtk-3-0 \
    libgirepository1.0-dev \
    gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 \
    libayatana-appindicator3-1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* ; \
    fi

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create directories for persistent data
RUN mkdir -p /app/data

# Copy application files
COPY *.py ./
COPY icons/ ./icons/
COPY lang/ ./lang/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DISPLAY=:0
ENV APP_DATA_DIR=/app/data

# Define volume for persistent data
VOLUME /app/data

# Run the application with GUI by default
CMD ["python", "main.py", "--tray", "--log"]