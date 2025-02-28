FROM python:3.12

# Set working directory
WORKDIR /app

# Copy all files
COPY . .

# Install dependencies
RUN pip install -r requirements.txt

# Ensure python3 is installed and create a symlink
RUN apt-get update && apt-get install -y python3 python3-pip && ln -s /usr/bin/python3 /usr/bin/python

# Install cron and rsyslog for logging
RUN apt-get update && apt-get install -y cron rsyslog && rm -rf /var/lib/apt/lists/*

# Ensure the cron log file exists
RUN touch /var/log/cron.log

# Copy cron job file
COPY cronjob /etc/cron.d/eve_cron

# Give proper permissions and register cron job
RUN chmod 0644 /etc/cron.d/eve_cron && crontab /etc/cron.d/eve_cron

# Ensure cron logs are enabled in rsyslog
RUN echo "cron.* /var/log/cron.log" >> /etc/rsyslog.conf

# Start rsyslog, cron, and keep container running
CMD service rsyslog start && cron && tail -f /var/log/cron.log
