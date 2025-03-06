# TwitchWatcher - Dockerized Twitch Drops Miner

![Docker](https://img.shields.io/badge/Docker-Ready-blue)
![Python](https://img.shields.io/badge/Python-3.10-green)
![License](https://img.shields.io/badge/License-MIT-yellow)

A containerized solution for mining Twitch drops without watching streams. This Docker implementation allows you to earn rewards while saving bandwidth and system resources.

<p align="center">
  <img src="https://raw.githubusercontent.com/yourusername/TwitchWatcher/main/icons/pickaxe.ico" width="150" alt="TwitchWatcher Logo">
</p>

## 🚀 Key Features

- **Bandwidth Efficient**: Only fetches stream metadata, no actual video streaming
- **Smart Channel Management**: Automatically switches channels when streams go offline
- **Priority System**: Focus on the drops you want with game priority and exclusion lists
- **Automatic Campaign Discovery**: Finds drops for all your linked accounts
- **Persistent Data**: Login sessions and settings stored in a Docker volume
- **Dual Mode Support**: Run with GUI for monitoring or headless on servers

## 📋 Prerequisites

- Docker Engine 20.10.0+
- Docker Compose 2.0.0+
- For GUI mode: X11 server access

## 🔧 Installation & Setup

### Clone & Prepare

```bash
# Clone the repository
git clone https://github.com/yourusername/TwitchWatcher.git
cd TwitchWatcher

# Ensure scripts are executable
chmod +x run.sh run-headless.sh
```

### GUI Mode

```bash
# Start with the convenience script
./run.sh

# Or manually with Docker Compose
docker-compose up -d
```

### Headless Mode (for servers)

```bash
# Start headless mode
./run-headless.sh

# Or manually with Docker Compose
docker-compose --profile headless up -d twitch-watcher-headless
```

## 🔍 Usage Guide

1. **First Run**: After starting the container, you'll need to login to your Twitch account
2. **Select Games**: Choose which games/drops to prioritize in the settings
3. **Monitor Progress**: Check the application interface or logs to monitor drop progress
4. **Data Location**: All persistent data is stored in the `./data` directory

For detailed instructions, see the [Docker Guide](DOCKER-GUIDE.md).

## ⚠️ Security Notes

> [!CAUTION]  
> Twitch authentication cookies are stored in the `data/` directory. Keep this directory secure to prevent unauthorized access to your account.

> [!IMPORTANT]  
> The application identifies as Chrome when connecting to Twitch. You may receive a "New Login" email from Twitch - this is normal.

## 🔄 Customization

The Docker setup can be customized in several ways:

```bash
# Build with custom tags
docker build -t custom-twitch-watcher .

# Run with custom environment variables
docker-compose up -d -e DISPLAY=:1
```

## 🛠️ Troubleshooting

Having issues? Try these steps:

1. **Check logs**: `docker-compose logs -f`
2. **Verify X11**: For GUI mode, ensure X server is properly configured
3. **Network Connectivity**: Make sure the container has internet access
4. **File Permissions**: Ensure the data directory is writable

## 🌐 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📜 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 👏 Acknowledgements

- Original [Twitch Drops Miner](https://github.com/DevilXD/TwitchDropsMiner) by DevilXD
- Thanks to all contributors who provided translations to the original project
- Docker implementation by [Your Name]

---

<p align="center">
  Made with ❤️ for the Twitch community
</p>