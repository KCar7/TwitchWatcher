# Contributing to TwitchWatcher

Thank you for considering contributing to TwitchWatcher! This document outlines the process for contributing to this Docker-based implementation of the Twitch Drops Miner.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment for everyone.

## How Can I Contribute?

### Reporting Bugs

This section guides you through submitting a bug report. Following these guidelines helps maintainers understand your report, reproduce the behavior, and find related reports.

- Use the bug report template provided in the `.github/ISSUE_TEMPLATE` directory.
- Provide as much detail as possible, including Docker version, host OS, and logs.
- Include steps to reproduce the issue.

### Suggesting Enhancements

This section guides you through submitting an enhancement suggestion, including completely new features and minor improvements to existing functionality.

- Use the feature request template provided in the `.github/ISSUE_TEMPLATE` directory.
- Clearly describe how your suggestion would improve the Docker implementation.
- Consider both GUI and headless mode usage scenarios.

### Pull Requests

- Fill out the pull request template.
- Keep pull requests focused on a single topic.
- Test your changes with both GUI and headless modes.
- Follow the same coding style as the rest of the project.
- Include tests when possible.

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/TwitchWatcher.git
   cd TwitchWatcher
   ```

2. Build the Docker images:
   ```bash
   # For GUI version
   docker build -t twitch-watcher-gui .
   
   # For headless version
   docker build --build-arg WITH_GUI=false -t twitch-watcher-headless .
   ```

3. Test your changes:
   ```bash
   # GUI version
   docker run -e DISPLAY=$DISPLAY -v /tmp/.X11-unix:/tmp/.X11-unix twitch-watcher-gui
   
   # Headless version
   docker run twitch-watcher-headless
   ```

## Docker-Specific Contributions

When contributing to this Docker implementation, please consider:

1. **Resource Efficiency**: Minimize image size and resource usage.
2. **Security**: Follow Docker security best practices.
3. **Compatibility**: Ensure the container works across different host platforms.
4. **Documentation**: Update documentation to reflect your changes.

## Styleguides

### Git Commit Messages

* Use the present tense ("Add feature" not "Added feature")
* Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
* Limit the first line to 72 characters or less
* Reference issues and pull requests liberally after the first line

### Python Styleguide

* Follow PEP 8 for Python code
* Use docstrings for functions and classes
* Keep code modular and maintainable

### Docker Styleguide

* Use multi-stage builds when appropriate
* Minimize the number of layers
* Group related RUN commands
* Use environment variables for configuration
* Document any exposed ports or volumes

## Additional Notes

### Issue and Pull Request Labels

| Label name | Description |
| --- | --- |
| `bug` | Confirmed bugs or reports likely to be bugs |
| `enhancement` | Feature requests |
| `documentation` | Documentation improvements |
| `docker` | Docker-specific changes |
| `help-wanted` | Issues that need assistance |
| `good-first-issue` | Good for newcomers |