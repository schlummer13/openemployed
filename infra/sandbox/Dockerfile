FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
RUN pip install --no-cache-dir requests==2.32.3
WORKDIR /app
COPY server.py runner.py ./
USER 10001
EXPOSE 8000
CMD ["python3", "-I", "/app/server.py"]
