FROM python:3.12-slim
WORKDIR /srv/smena
COPY pyproject.toml ./
COPY requirements.lock ./
COPY app ./app
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . && useradd --create-home smena
COPY alembic.ini ./
COPY migrations ./migrations
COPY main.py ./
RUN mkdir -p /app/data && chown smena:smena /app/data
EXPOSE 8000
CMD ["python", "main.py"]
