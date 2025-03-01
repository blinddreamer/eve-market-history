FROM python:3.12

# Set working directory
WORKDIR /app

# Copy all files
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Install cron
RUN apt-get update && apt-get install -y cron && rm -rf /var/lib/apt/lists/*

# Ensure log file exists
RUN touch /var/log/cron.log

# Copy cron job file
COPY cronjob /etc/cron.d/eve_cron

# Give execution permissions
RUN chmod 0644 /etc/cron.d/eve_cron && crontab /etc/cron.d/eve_cron

# Ensure environment variables are available to cron
RUN printenv > /etc/environment

# Start cron and keep the container running
CMD cron && tail -f /var/log/cron.log
