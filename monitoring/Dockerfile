# Prometheus with the Rackphone scrape config baked in.
#
# Mounting prometheus.yml would be the obvious thing, and it does not work
# against a remote Docker context: the daemon resolves the path on its own
# filesystem. Baking it into an image means the config travels with the build
# context, which is the one thing that does cross.
FROM prom/prometheus:latest

COPY prometheus.yml /etc/prometheus/prometheus.yml
