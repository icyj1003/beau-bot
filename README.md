# Beau

A bot for Discord with basic features such as music streaming, and playlist management, ...

## Use with Docker

Add TOKEN to Dockerfile

    ENV TOKEN <YOUR_TOKEN>

then

    docker build -t beau .
    docker run beau

or

    docker build -t beau .
    docker run --env TOKEN=<YOUR_TOKEN> beau
