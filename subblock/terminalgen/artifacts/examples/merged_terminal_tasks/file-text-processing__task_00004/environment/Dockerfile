FROM ubuntu:22.04
WORKDIR /app
RUN apt-get update && apt-get install -y jq && rm -rf /var/lib/apt/lists/*
COPY ./task_file /app/task_file