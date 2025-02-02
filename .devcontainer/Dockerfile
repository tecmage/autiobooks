FROM ubuntu AS base

RUN apt-get update && apt-get install -y \
    curl \
    python3 \
    git
# install poetry
RUN curl -sSL https://install.python-poetry.org | python3 -

# install poetry dependencies
RUN PATH=$PATH:/root/.local/bin