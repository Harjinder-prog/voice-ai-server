FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# No EXPOSE or CMD needed here, as main.py handles it.
# But we can still keep the CMD for clarity.
CMD ["python", "main.py"]
