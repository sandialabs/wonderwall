FROM python:3.13-trixie

# Build args for user configuration
ARG USERNAME=vscode
ARG USER_UID=1000
ARG USER_GID=1000

# Install system dependencies and generate en_US.UTF-8 locale
RUN apt-get update && apt-get install -y \
    curl \
    git \
    sudo \
    bash-completion \
    ca-certificates \
    pipx \
    locales \
    && echo "en_US.UTF-8 UTF-8" > /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# Create group and user with provided UID/GID
RUN groupadd --gid "${USER_GID}" "${USERNAME}" \
    && useradd \
    --uid "${USER_UID}" \
    --gid "${USER_GID}" \
    --shell /bin/bash \
    --create-home \
    "${USERNAME}"

# Configure passwordless sudo for the vscode user
RUN echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

# Switch to the vscode user to install Claude Code into their home dir
USER ${USERNAME}
WORKDIR /home/${USERNAME}

# Install Claude Code using the native installer (no Node.js dependency)
# The installer places the binary at ~/.local/bin/claude
RUN curl -fsSL https://claude.ai/install.sh | bash

# Install poetry via pipx
# pipx installs into ~/.local/bin, same dir Claude Code uses
RUN pipx install poetry

# Ensure ~/.local/bin is on PATH for interactive and non-interactive shells
RUN echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/${USERNAME}/.bashrc \
    && echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/${USERNAME}/.profile

# Create mount-point stubs so Docker volume mounts land with correct ownership.
# The actual persistent data lives in named volumes defined in devcontainer.json.
RUN mkdir -p \
    /home/${USERNAME}/.claude \
    /home/${USERNAME}/.bash_history_dir

# Configure bash to write history to a file inside the persisted volume dir,
# and to append rather than overwrite on exit.
RUN echo '' >> /home/${USERNAME}/.bashrc \
    && echo '# Persist bash history to mounted volume' >> /home/${USERNAME}/.bashrc \
    && echo 'export HISTFILE="$HOME/.bash_history_dir/.bash_history"' >> /home/${USERNAME}/.bashrc \
    && echo 'export HISTSIZE=10000' >> /home/${USERNAME}/.bashrc \
    && echo 'export HISTFILESIZE=50000' >> /home/${USERNAME}/.bashrc \
    && echo 'shopt -s histappend' >> /home/${USERNAME}/.bashrc \
    && echo 'PROMPT_COMMAND="history -a${PROMPT_COMMAND:+; $PROMPT_COMMAND}"' >> /home/${USERNAME}/.bashrc