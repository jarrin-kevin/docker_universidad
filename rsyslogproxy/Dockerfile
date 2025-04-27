# File: Dockerfile.rsyslog
FROM alpine:3.19

# Install rsyslog and required modules
RUN apk add --no-cache rsyslog

# Create log directory to prevent rsyslog warnings
RUN mkdir -p /var/log

# Run rsyslog in foreground
CMD ["rsyslogd", "-n"]
