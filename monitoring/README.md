# Monitoring

**Prometheus and Grafana both run outside the Rackphone stack**, because each
usually watches more than this rack. The files here are what they are
configured with; the only thing they need from the stack is the bridge on
`:9105`.

> [!IMPORTANT]
> If your Prometheus or Grafana runs against a **remote** Docker daemon, these
> files cannot be bind-mounted into it: the daemon resolves the path on its own
> filesystem. Copy them into the container instead, and repeat after an edit:
>
> ```sh
> docker cp prometheus.yml <container>:/etc/prometheus/
> docker restart <container>
> ```


## Layout

| Path | For |
| --- | --- |
| `prometheus.yml` | Scrape config, mounted into the Prometheus container |
| `grafana/provisioning/datasources/` | Datasource definition |
| `grafana/provisioning/dashboards/` | Dashboard provider |
| `grafana/dashboards/*.json` | The four dashboards |
| `rules/rackphone.yml` | Alerting rules, for a Prometheus that has an Alertmanager |
| `generate-dashboards.py` | Regenerates those JSON files |

## Scraping from a Prometheus you already run

One job covers every phone: the `unit` label comes from the bridge, so adding a
second phone changes nothing here.

```yaml
  - job_name: rackphone
    # A scrape is a USB round-trip to the phone. Typical is under a second, and
    # the wider window is so that one wedged unit cannot fail the job.
    scrape_interval: 30s
    scrape_timeout: 20s
    static_configs:
      - targets: ["<host running the bridge>:9105"]
```

The rules in `rules/` assume that job. Copy the file into the rules directory
that Prometheus already loads — every alert names the unit it is about, and
`RackphoneBalanceStale` is what keeps the two balance alerts honest when a SIM
stops answering altogether.

## Pointing an external Grafana at this

Mount `grafana/provisioning/` and `grafana/dashboards/` into your Grafana and
set the Prometheus URL. Nothing here needs editing — the datasource reads
`RACKPHONE_PROMETHEUS_URL`, and defaults to loopback for a Grafana running
natively on the same host as the stack.

```sh
docker run -d --name grafana \
  -p 3000:3000 \
  -e RACKPHONE_PROMETHEUS_URL=http://host.docker.internal:9090 \
  --add-host host.docker.internal:host-gateway \
  -v "$PWD/monitoring/grafana/provisioning:/etc/grafana/provisioning:ro" \
  -v "$PWD/monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro" \
  grafana/grafana:11.5.1
```

For a Grafana installed natively on this host, the default is already correct:

```sh
sudo cp -r monitoring/grafana/provisioning/* /etc/grafana/provisioning/
sudo cp -r monitoring/grafana/dashboards /var/lib/grafana/
sudo systemctl restart grafana-server
```

## Reaching Prometheus from another machine

Prometheus publishes on `127.0.0.1:9090` only, so it is unreachable from
anywhere else by default. **Prometheus has no authentication**, so widening that
exposes every metric and the admin API to whatever can route to the port. Put a
firewall or reverse proxy in front rather than binding it to the world:

```yaml
    ports:
      - "10.0.0.5:9090:9090"   # a specific interface, never 0.0.0.0
```

## Dashboards

| Dashboard | Question it answers | Default range |
| --- | --- | --- |
| `rackphone-fleet` | Is anything wrong, across every unit? | 12h |
| `rackphone-unit` | What is one unit doing right now? | 6h |
| `rackphone-battery` | How are the packs ageing? | 30d |

The provider sets `allowUiUpdates: false`, so edits made in the Grafana UI are
overwritten on reload. Change `generate-dashboards.py` and re-run it instead:

```sh
python3 monitoring/generate-dashboards.py
```

**One rule the Fleet dashboard depends on:** every panel there must return
exactly one series per unit. Colour follows the entity via
`palette-classic-by-name`, which hashes the series *name* — so two series
sharing a legend get the same colour and become indistinguishable. Anything
carrying an extra dimension (thermal zone, SIM slot) is aggregated with
`by (unit)`. Per-zone and per-slot detail belongs on the Unit dashboard, where
there is one unit and that dimension is the thing being compared.
