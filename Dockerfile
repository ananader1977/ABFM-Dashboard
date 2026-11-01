FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py analytics.py measures.py security.py import_pbix.py brand.py render_start.py data_access.py parquet_layout.py visuals.py executive.py ./
COPY .streamlit/config.toml .streamlit/config.toml
ENV ABFM_DATA_DIR=/var/data PORT=10000
EXPOSE 10000
CMD ["python", "render_start.py"]
