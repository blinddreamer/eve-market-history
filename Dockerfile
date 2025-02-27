FROM python:3.12

# Set working directory
WORKDIR /app

# Copy all files
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Copy cron job file
COPY cronjob /etc/cron.d/eve_cron

# Apply cron job
RUN chmod 0644 /etc/cron.d/eve_cron && crontab /etc/cron.d/eve_cron

# Start cron and the script
CMD touch /var/log/cron.log && cron && tail -f /var/log/cron.log