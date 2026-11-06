FROM python:3.10-slim
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Copy all files
COPY . .

# Install the app using pyproject.toml instead of requirements.txt
RUN pip install --no-cache-dir .

# Expose port and start
ENV PORT=7860
EXPOSE 7860
# Note: We will need to find the exact start command, but let's try this first
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
