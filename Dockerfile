FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 voyage
COPY --chown=voyage:voyage SmartVoyage/ ./SmartVoyage/
RUN mkdir -p /app/SmartVoyage/var && chown voyage:voyage /app/SmartVoyage/var
USER voyage
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"
CMD ["python", "-m", "SmartVoyage.services.stack", "--host", "0.0.0.0"]
