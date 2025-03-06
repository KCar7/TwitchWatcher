# Security Policy

## Supported Versions

We currently support the following versions with security updates:

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability within TwitchWatcher, please send an email to security@yourdomain.com. All security vulnerabilities will be promptly addressed.

Please include the following information in your report:

- Type of vulnerability
- Steps to reproduce
- Affected version(s)
- Potential impact

## Security Considerations

### Authentication Data

The application stores Twitch authentication cookies in the data volume. Protect this data by:

1. Ensuring the data directory has proper permissions
2. Not sharing the data directory with untrusted parties
3. Using volume encryption when possible
4. Regularly backing up and rotating credentials

### Container Security

When running the Docker container:

1. Do not run the container as root
2. Keep the Docker engine and images updated
3. Use network isolation when possible
4. Scan images for vulnerabilities

### X11 Security (GUI Mode)

When using the GUI mode with X11 forwarding:

1. Use X11 cookies for authentication
2. Consider using SSH X11 forwarding for remote access
3. Be aware that X11 forwarding can have security implications

## Acknowledgments

We would like to thank the following individuals who have reported security vulnerabilities:

- *None reported yet*