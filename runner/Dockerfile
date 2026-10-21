# syntax=docker/dockerfile:1
#
# The runner sidecar. Tiny on purpose: the standard library plus the Docker
# SDK. It holds the Docker socket, so it is the one image that must stay small
# and boring. See runner/app.py for what it accepts and why.
FROM python:3.14-slim
WORKDIR /runner
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
ENV PYTHONUNBUFFERED=1
EXPOSE 7000
CMD ["python", "app.py"]
