FROM python:3.12

# Set working directory
WORKDIR /app

# Copy all files
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Run script directly without cron
CMD ["python3", "/app/server.py"]
