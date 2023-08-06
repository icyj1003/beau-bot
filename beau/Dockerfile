# For more information, please refer to https://aka.ms/vscode-docker-python
FROM python:3.11-slim

# Install pip requirements
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN apt-get -y update
RUN apt-get update && apt-get install -y ffmpeg

WORKDIR /app
COPY functional.py /app
COPY main.py /app
COPY music.py /app

# During debugging, this entry point will be overridden. For more information, please refer to https://aka.ms/vscode-docker-python-debug
CMD ["python", "-u", "main.py"]