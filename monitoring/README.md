# Monitoring

Prometheus and Grafana both run in the compose stack, under the `monitoring`
profile. The files here are what they are configured with.

> [!IMPORTANT]
> **Nothing here is mounted.** The stack is driven through a remote Docker
> context, where a bind mount is resolved on the daemon's filesystem and finds
> nothing of yours. Both services keep their configuration in named volumes,
> which are filled once and refilled after an edit:
>
> ```sh
> docker compose cp prometheus.yml prometheus:/etc/prometheus/
> docker compose cp grafana/provisioning/. grafana:/etc/grafana/provisioning/
> docker compose cp grafana/dashboards/. grafana:/var/lib/grafana/dashboards/
> docker compose --profile monitoring restart
> ```


## Layout

| Path | For |
| --- | --- |
| `prometheus.yml` | Scrape config, mounted into the Prometheus container |
| `grafana/provisioning/datasources/` | Datasource definition |
| `grafana/provisioning/dashboards/` | Dashboard provider |
| `grafana/dashboards/*.json` | The four dashboards |
| `generate-dashboards.py` | Regenerates those JSON files |

## Pointing an external Grafana at this

Copy the same two directories into a Grafana that lives elsewhere and set the
Prometheus URL. Nothing here needs editing — the datasource reads
`RACKPHONE_PROMETHEUS_URL`, which the compose service already sets to the
Prometheus beside it.

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
