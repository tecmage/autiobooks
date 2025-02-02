FROM ubuntu AS base

RUN apt-get update && apt-get install -y \
    curl \
    python3 \
    git \
    python3-pip \
    python3-tk
# install poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
