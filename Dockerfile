FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 tripweave
COPY --chown=tripweave:tripweave TripWeave/ ./TripWeave/
RUN mkdir -p /app/TripWeave/var && chown tripweave:tripweave /app/TripWeave/var
USER tripweave
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"
CMD ["python", "-m", "TripWeave.services.stack", "--host", "0.0.0.0"]
