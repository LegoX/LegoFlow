FROM ubuntu:22.04
WORKDIR /app
RUN apt-get update && apt-get install -y build-essential libomp-dev && rm -rf /var/lib/apt/lists/*
COPY ./task_file /app/task_file