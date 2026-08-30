"""Generate the Rackphone Grafana dashboards.

Three dashboards rather than one, because they answer different questions:
  fleet   - is anything wrong, across every unit?
  unit    - what is one unit doing right now?
  battery - how are the packs ageing, over weeks?

Design rules applied throughout:
  * One measure per axis. Never two y-scales - RSRP/RSRQ/SINR get their own
    panels rather than sharing one and misrepresenting all three.
  * Colour follows the entity. Multi-unit series use palette-classic-by-name so
    a unit dropping out never repaints the survivors.
  * Magnitude gets a sequential ramp; state gets reserved status colours.
  * Legends always present for >=2 series; no value printed on every point.
"""
import json, pathlib

DS = {"type": "prometheus", "uid": "rackphone-prom"}
# Beside this script, so a checkout anywhere regenerates in place.
OUT = pathlib.Path(__file__).resolve().parent / "grafana" / "dashboards"

BY_NAME = {"mode": "palette-classic-by-name"}
THRESH = {"mode": "thresholds"}
FIXED = lambda c: {"mode": "fixed", "fixedColor": c}
SEQ = {"mode": "continuous-GrYlRd"}

LINE = {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0, "showPoints": "never",
        "spanNulls": True, "axisBorderShow": False, "gradientMode": "none",
        "lineInterpolation": "smooth", "pointSize": 8,
        "scaleDistribution": {"type": "linear"},
        "hideFrom": {"legend": False, "tooltip": False, "viz": False}}
AREA = dict(LINE, fillOpacity=10, gradientMode="opacity")

ONLINE = [{"type": "value", "options": {
    "0": {"color": "red", "index": 1, "text": "DOWN"},
    "1": {"color": "green", "index": 0, "text": "UP"}}}]
OKDEAD = [{"type": "value", "options": {
    "0": {"color": "red", "index": 1, "text": "DEAD"},
    "1": {"color": "green", "index": 0, "text": "OK"}}}]
YESNO = [{"type": "value", "options": {
    "0": {"color": "dark-blue", "index": 0, "text": "no"},
    "1": {"color": "green", "index": 1, "text": "yes"}}}]

def steps(*pairs):
    out = [{"color": pairs[0][0], "value": None}]
    for color, value in pairs[1:]:
        out.append({"color": color, "value": value})
    return out

def target(expr, legend="", ref="A", instant=False, fmt=None):
    t = {"datasource": DS, "editorMode": "code", "expr": expr, "refId": ref,
         "legendFormat": legend, "range": not instant, "instant": instant}
    if fmt:
        t["format"] = fmt
    return t

def fc(unit=None, mn=None, mx=None, dec=None, color=None, custom=None,
       mappings=None, thresholds=None, links=None, nodata=None):
    d = {"color": color or BY_NAME, "mappings": mappings or [],
         "thresholds": {"mode": "absolute",
                        "steps": thresholds or [{"color": "text", "value": None}]}}
    if unit: d["unit"] = unit
    if mn is not None: d["min"] = mn
    if mx is not None: d["max"] = mx
    if dec is not None: d["decimals"] = dec
    if custom: d["custom"] = custom
    if links: d["links"] = links
    if nodata: d["noValue"] = nodata
    return d

class Layout:
    """Tracks grid position so panels cannot silently overlap."""
    def __init__(self):
        self.panels, self.y, self.x, self.pid = [], 0, 0, 0

    def row(self, title, collapsed=False):
        if self.x:
            self.newline()
        self.pid += 1
        self.panels.append({"id": self.pid, "type": "row", "title": title,
                            "collapsed": collapsed, "panels": [],
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}})
        self.y += 1

    def newline(self, h=0):
        self.y += h
        self.x = 0

    def add(self, title, ptype, w, h, targets, fieldConfig, options=None,
            desc=None, transformations=None, overrides=None, links=None):
        if self.x + w > 24:
            self.x = 0
            self.y += h
        self.pid += 1
        p = {"id": self.pid, "title": title, "type": ptype, "datasource": DS,
             "gridPos": {"x": self.x, "y": self.y, "w": w, "h": h},
             "targets": targets, "options": options or {},
             "fieldConfig": {"defaults": fieldConfig, "overrides": overrides or []}}
        if desc: p["description"] = desc
        if transformations: p["transformations"] = transformations
        if links: p["links"] = links
        self.panels.append(p)
        self.x += w
        if self.x >= 24:
            self.x = 0
            self.y += h
        return p

def stat(mode="background", graph="none"):
    return {"colorMode": mode, "graphMode": graph, "justifyMode": "auto",
            "textMode": "auto", "wideLayout": True, "showPercentChange": False,
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}}

TS = {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []},
      "tooltip": {"mode": "multi", "sort": "desc", "hideZeros": False}}
TS_TABLE = {"legend": {"displayMode": "table", "placement": "right", "showLegend": True,
                       "calcs": ["lastNotNull", "min", "max"]},
            "tooltip": {"mode": "multi", "sort": "desc", "hideZeros": False}}
TIMELINE = {"showValue": "never", "mergeValues": True, "rowHeight": 0.85,
            "alignValue": "center",
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "single", "sort": "none"}}

def dashboard(uid, title, desc, panels, variables=None, time_from="now-6h",
              refresh="30s", links=None):
    return {
        "uid": uid, "title": title, "description": desc,
        "tags": ["rackphone"], "timezone": "browser", "schemaVersion": 39,
        "version": 1, "refresh": refresh, "editable": True, "graphTooltip": 1,
        "time": {"from": time_from, "to": "now"},
        "templating": {"list": variables or []},
        "links": links or [],
        "panels": panels,
    }

NAV = [
    {"type": "dashboards", "title": "Rackphone", "tags": ["rackphone"],
     "asDropdown": True, "icon": "external link", "includeVars": True,
     "keepTime": True, "targetBlank": False},
]

def unit_var(multi=True, all_=True):
    return {
        "name": "unit", "label": "Unit", "type": "query", "datasource": DS,
        "definition": "label_values(rackphone_up, unit)",
        "query": {"qryType": 1, "query": "label_values(rackphone_up, unit)", "refId": "unit"},
        "refresh": 1, "includeAll": all_, "multi": multi, "allValue": ".*",
        "current": {"selected": False, "text": "All" if all_ else "", "value": "$__all" if all_ else ""},
        "sort": 1, "hide": 0,
    }

U = '{unit=~"$unit"}'

# ===========================================================================
# 1. FLEET - every unit at a glance
# ===========================================================================
L = Layout()
L.row("Fleet")

L.add("Units up", "stat", 8, 5,
      [target("sum(rackphone_up)", "up", "A", instant=True),
       target("count(rackphone_up)", "total", "B", instant=True)],
      fc(dec=0, color=THRESH, thresholds=steps(("red", None), ("green", 1))),
      stat("value"), "Left number is reachable units; right is configured units.")

L.add("Any unit down", "stat", 8, 5,
      [target("count(rackphone_up == 0) or vector(0)", "down", "A", instant=True)],
      fc(dec=0, color=THRESH, thresholds=steps(("green", None), ("red", 1))),
      stat(), "Reserved status colour: red means act, not 'series 2'.")

L.add("Guards dead", "stat", 8, 5,
      [target("count(rackphone_battery_guard_up == 0) or vector(0)", "dead", "A", instant=True)],
      fc(dec=0, color=THRESH, thresholds=steps(("green", None), ("red", 1))),
      stat(),
      "A dead guard while charging is suspended means a unit is silently discharging.")

L.row("Fleet health")
L.add("Weakest signal", "stat", 8, 5,
      [target("min(rackphone_lte_rsrp_dbm)", "rsrp", "A", instant=True)],
      fc(unit="dBm", dec=0, color=THRESH,
         thresholds=steps(("red", None), ("orange", -110), ("yellow", -100), ("green", -90))),
      stat(), "Worst RSRP across the fleet. >-90 good, -100..-90 fair, <-110 poor.")

L.add("Hottest unit", "stat", 8, 5,
      [target('max(rackphone_temperature_celsius{zone=~"msm-skin-therm-usr|quiet_therm"}) by (unit)',
              "{{unit}}", "A", instant=True)],
      fc(unit="celsius", dec=1, color=THRESH,
         thresholds=steps(("green", None), ("yellow", 45), ("orange", 55), ("red", 65))),
      stat(), "Skin temperature, which is what a rack full of phones actually heats up.")

L.add("Lowest health", "stat", 8, 5,
      [target("min(rackphone_battery_health_ratio) * 100", "soh", "A", instant=True)],
      fc(unit="percent", dec=1, color=THRESH,
         thresholds=steps(("red", None), ("orange", 70), ("yellow", 85), ("green", 92))),
      stat(), "charge_full / charge_full_design across the fleet.")

L.row("Per unit")

# The table is the centrepiece of a multi-unit view: one row per device, joined
# from separate instant queries on the shared `unit` label.
UNIT_LINK = [{"title": "Open unit dashboard", "url": "/d/rackphone-unit/rackphone-unit?var-unit=${__value.text}"}]
L.add("Fleet summary", "table", 24, 8, [
        target("rackphone_up", "", "A", instant=True, fmt="table"),
        target("rackphone_battery_capacity_percent", "", "B", instant=True, fmt="table"),
        target("rackphone_battery_health_ratio * 100", "", "C", instant=True, fmt="table"),
        target("rackphone_battery_guard_up", "", "D", instant=True, fmt="table"),
        target('max(rackphone_temperature_celsius{zone=~"msm-skin-therm-usr|quiet_therm"}) by (unit)', "", "E", instant=True, fmt="table"),
        target("rackphone_lte_rsrp_dbm", "", "F", instant=True, fmt="table"),
        target("rackphone_uptime_seconds", "", "G", instant=True, fmt="table"),
        target("rackphone_battery_charging_suspended", "", "H", instant=True, fmt="table"),
      ],
      fc(color=THRESH, custom={"align": "auto", "cellOptions": {"type": "auto"},
                               "filterable": False, "inspect": False}),
      {"showHeader": True, "cellHeight": "sm", "footer": {"show": False, "reducer": ["sum"], "countRows": False, "fields": ""}},
      "One row per device. Click a unit name to open its detail dashboard.",
      transformations=[
        {"id": "joinByField", "options": {"byField": "unit", "mode": "outer"}},
        {"id": "organize", "options": {
            "excludeByName": {"Time": True, "Time 1": True, "Time 2": True, "Time 3": True,
                              "Time 4": True, "Time 5": True, "Time 6": True, "Time 7": True,
                              "Time 8": True, "__name__": True, "job": True, "instance": True,
                              "slot": True, "monitor": True},
            "renameByName": {
                "unit": "Unit", "Value #A": "State", "Value #B": "Charge",
                "Value #C": "Health", "Value #D": "Guard", "Value #E": "Skin temp",
                "Value #F": "RSRP", "Value #G": "Uptime", "Value #H": "Suspended"},
            "indexByName": {}}},
      ],
      overrides=[
        {"matcher": {"id": "byName", "options": "Unit"},
         "properties": [{"id": "links", "value": UNIT_LINK},
                        {"id": "custom.width", "value": 140}]},
        {"matcher": {"id": "byName", "options": "State"},
         "properties": [{"id": "mappings", "value": ONLINE},
                        {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": steps(("red", None), ("green", 1))}}]},
        {"matcher": {"id": "byName", "options": "Charge"},
         "properties": [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 0},
                        {"id": "max", "value": 100}, {"id": "min", "value": 0},
                        {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "gradient"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": steps(("red", None), ("orange", 20), ("green", 40), ("yellow", 90))}}]},
        {"matcher": {"id": "byName", "options": "Health"},
         "properties": [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 1},
                        {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": steps(("red", None), ("orange", 70), ("yellow", 85), ("green", 92))}}]},
        {"matcher": {"id": "byName", "options": "Guard"},
         "properties": [{"id": "mappings", "value": OKDEAD},
                        {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": steps(("red", None), ("green", 1))}}]},
        {"matcher": {"id": "byName", "options": "Suspended"},
         "properties": [{"id": "mappings", "value": YESNO},
                        {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": steps(("text", None))}}]},
        {"matcher": {"id": "byName", "options": "Skin temp"},
         "properties": [{"id": "unit", "value": "celsius"}, {"id": "decimals", "value": 1},
                        {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": steps(("green", None), ("yellow", 45), ("orange", 55), ("red", 65))}}]},
        {"matcher": {"id": "byName", "options": "RSRP"},
         "properties": [{"id": "unit", "value": "dBm"}, {"id": "decimals", "value": 0},
                        {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                        {"id": "thresholds", "value": {"mode": "absolute", "steps": steps(("red", None), ("orange", -110), ("yellow", -100), ("green", -90))}}]},
        {"matcher": {"id": "byName", "options": "Uptime"},
         "properties": [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 0}]},
      ])

L.row("Comparison")
# Every panel here must yield exactly ONE series per unit. Colour follows the
# entity via palette-classic-by-name, which hashes the series *name* - so two
# series sharing a legend get the same colour and become indistinguishable.
# Anything with extra dimensions (thermal zone, SIM slot) is therefore
# aggregated away rather than plotted raw. Per-zone and per-slot detail belongs
# on the Unit dashboard, where there is one unit and the extra dimension is the
# thing being compared.
for title, expr, unit_, dec, custom, desc in [
    ("Charge", "rackphone_battery_capacity_percent", "percent", 0, AREA,
     "Units drifting apart here usually means one is not getting enough USB current."),
    ("Skin temperature",
     'max(rackphone_temperature_celsius{zone=~"msm-skin-therm-usr|quiet_therm"}) by (unit)',
     "celsius", 1, LINE,
     "Hottest of the skin sensors per unit. The individual zones are on the Unit dashboard."),
    ("LTE RSRP", "min(rackphone_lte_rsrp_dbm) by (unit)", "dBm", 0, LINE,
     "Weakest slot per unit, matching the Weakest signal tile. Per-slot detail is on the Unit dashboard."),
    ("Collect cost", "rackphone_collect_duration_seconds", "s", 3, LINE,
     "Rising toward the 10s scrape timeout means the USB link is degrading."),
]:
    L.add(title, "timeseries", 12, 7,
          [target(expr, "{{unit}}", "A")],
          fc(unit=unit_, dec=dec, custom=custom, color=BY_NAME), TS, desc)

fleet = dashboard("rackphone-fleet", "Rackphone / Fleet",
                  "Every unit at a glance. Start here.", L.panels,
                  variables=[], time_from="now-12h", links=NAV)

# ===========================================================================
# 2. UNIT - one device in detail
# ===========================================================================
L = Layout()
L.row("Overview")
L.add("State", "stat", 6, 5, [target(f"rackphone_up{U}", "{{unit}}", "A", instant=True)],
      fc(color=THRESH, mappings=ONLINE, thresholds=steps(("red", None), ("green", 1))), stat())
L.add("Charge", "gauge", 6, 5,
      [target(f"rackphone_battery_capacity_percent{U}", "{{unit}}", "A", instant=True)],
      fc(unit="percent", mn=0, mx=100, dec=0, color=THRESH,
         thresholds=steps(("red", None), ("orange", 20), ("green", 40), ("yellow", 90))),
      {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
       "showThresholdMarkers": True, "showThresholdLabels": False},
      "Yellow above 90% is deliberate: a cabled pack should not live up there.")
L.add("Health", "stat", 6, 5,
      [target(f"rackphone_battery_health_ratio{U} * 100", "{{unit}}", "A", instant=True)],
      fc(unit="percent", dec=1, color=THRESH,
         thresholds=steps(("red", None), ("orange", 70), ("yellow", 85), ("green", 92))), stat())
L.add("Guard", "stat", 6, 5,
      [target(f"rackphone_battery_guard_up{U}", "{{unit}}", "A", instant=True)],
      fc(color=THRESH, mappings=OKDEAD, thresholds=steps(("red", None), ("green", 1))), stat())
L.row("Signal and access")
L.add("Uptime", "stat", 8, 5, [target(f"rackphone_uptime_seconds{U}", "{{unit}}", "A", instant=True)],
      fc(unit="s", dec=0, color=FIXED("text")), stat("none"))
L.add("RSRP", "stat", 8, 5, [target(f"rackphone_lte_rsrp_dbm{U}", "slot {{slot}}", "A", instant=True)],
      fc(unit="dBm", dec=0, color=THRESH,
         thresholds=steps(("red", None), ("orange", -110), ("yellow", -100), ("green", -90))), stat())
L.add("Root", "stat", 8, 5, [target(f"rackphone_root_available{U}", "{{unit}}", "A", instant=True)],
      fc(color=THRESH,
         mappings=[{"type": "value", "options": {"0": {"color": "orange", "index": 1, "text": "NO ROOT"},
                                                 "1": {"color": "green", "index": 0, "text": "ROOT"}}}],
         thresholds=steps(("orange", None), ("green", 1))), stat(),
      "Without root the guard cannot run and several series disappear.")

L.row("Battery")
L.add("Charge level and window", "timeseries", 12, 8, [
        target(f"rackphone_battery_capacity_percent{U}", "charge", "A"),
        target(f'rackphone_battery_window_percent{{unit=~"$unit",bound="max"}}', "stop at", "B"),
        target(f'rackphone_battery_window_percent{{unit=~"$unit",bound="min"}}', "resume below", "C")],
      fc(unit="percent", mn=0, mx=100, dec=0, custom=AREA), TS,
      "Window bounds are plotted as series, so a config change is visible on the chart.",
      overrides=[
        {"matcher": {"id": "byName", "options": "stop at"},
         "properties": [{"id": "color", "value": FIXED("orange")},
                        {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}},
                        {"id": "custom.fillOpacity", "value": 0}]},
        {"matcher": {"id": "byName", "options": "resume below"},
         "properties": [{"id": "color", "value": FIXED("blue")},
                        {"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [8, 6]}},
                        {"id": "custom.fillOpacity", "value": 0}]}])
L.add("Current", "timeseries", 6, 8, [target(f"rackphone_battery_current_amperes{U}", "current", "A")],
      fc(unit="amp", dec=3, custom=AREA), TS,
      "Sign convention is vendor-defined on this kernel; read direction as relative.")
L.add("Voltage", "timeseries", 6, 8, [target(f"rackphone_battery_voltage_volts{U}", "voltage", "A")],
      fc(unit="volt", dec=3, custom=LINE), TS)
L.add("Charging state", "state-timeline", 12, 6, [
        target(f"rackphone_battery_charging_suspended{U}", "guard suspended", "A"),
        target(f"rackphone_battery_charging{U}", "charging", "B")],
      fc(color=THRESH, mappings=YESNO, thresholds=steps(("dark-blue", None), ("green", 1))),
      TIMELINE, "State, not magnitude - a timeline reads better than two flat lines at 0 and 1.")
L.add("Battery and connector temperature", "timeseries", 12, 6, [
        target(f"rackphone_battery_temperature_celsius{U}", "battery", "A"),
        target(f"rackphone_connector_temperature_celsius{U}", "connector", "B")],
      fc(unit="celsius", dec=1, custom=LINE), TS,
      "Connector temperature is the one that matters for a phone left cabled for months.")

L.row("Thermal")
KEY = "battery|msm-skin-therm-usr|quiet_therm|cpuss-0-usr|gpuss-0-usr|ddr-usr|mdmss-0-usr|charger_therm0"
L.add("Key zones", "timeseries", 14, 9,
      [target(f'rackphone_temperature_celsius{{unit=~"$unit",zone=~"{KEY}"}}', "{{zone}}", "A")],
      fc(unit="celsius", dec=1, custom=LINE), TS_TABLE,
      "Eight zones, not all 23: past roughly eight series a line chart stops being readable.")
L.add("All zones, latest", "bargauge", 10, 9,
      [target(f"rackphone_temperature_celsius{U}", "{{zone}}", "A", instant=True)],
      fc(unit="celsius", dec=1, mn=20, mx=60, color=SEQ),
      {"displayMode": "gradient", "orientation": "horizontal", "showUnfilled": True,
       "minVizWidth": 8, "minVizHeight": 10, "valueMode": "color", "sizing": "auto",
       "namePlacement": "auto", "maxVizHeight": 300,
       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
      "Temperature is magnitude, so one sequential ramp rather than 23 cycled hues.")

L.row("System")
L.add("CPU utilisation", "timeseries", 8, 8,
      [target(f'100 * (1 - rate(rackphone_cpu_seconds_total{{unit=~"$unit",mode="idle"}}[5m]) '
              f'/ ignoring(mode) sum without(mode)(rate(rackphone_cpu_seconds_total{U}[5m])))', "busy", "A")],
      fc(unit="percent", mn=0, mx=100, dec=1, custom=AREA, color=FIXED("green")), TS)
L.add("Memory", "timeseries", 8, 8, [
        # `ignoring(kind)` is required: without it the two sides carry different
        # label sets and Prometheus matches nothing, silently returning empty.
        target(f'rackphone_memory_bytes{{unit=~"$unit",kind="total"}} - ignoring(kind) '
               f'rackphone_memory_bytes{{unit=~"$unit",kind="available"}}', "used", "A"),
        target(f'rackphone_memory_bytes{{unit=~"$unit",kind="total"}}', "total", "B")],
      fc(unit="bytes", custom=AREA), TS)
L.add("Load average", "timeseries", 8, 8, [
        target(f"rackphone_load1{U}", "1m", "A"),
        target(f"rackphone_load5{U}", "5m", "B"),
        target(f"rackphone_load15{U}", "15m", "C")],
      fc(dec=2, mn=0, custom=LINE), TS)
L.add("Storage /data", "timeseries", 8, 7, [
        target(f'rackphone_filesystem_bytes{{unit=~"$unit",kind="used"}}', "used", "A"),
        target(f'rackphone_filesystem_bytes{{unit=~"$unit",kind="size"}}', "size", "B")],
      fc(unit="bytes", custom=AREA), TS)
L.add("Network throughput", "timeseries", 16, 7, [
        target(f'rate(rackphone_network_bytes_total{{unit=~"$unit",direction="rx"}}[2m]) * 8', "{{interface}} rx", "A"),
        target(f'rate(rackphone_network_bytes_total{{unit=~"$unit",direction="tx"}}[2m]) * 8', "{{interface}} tx", "B")],
      fc(unit="bps", custom=AREA), TS_TABLE,
      "Counters are stored and the rate computed at query time - never store a pre-computed rate.")

L.row("Radio")
for title, lte, nr, unit_, desc in [
    ("RSRP", "rackphone_lte_rsrp_dbm", "rackphone_nr_ss_rsrp_dbm", "dBm",
     "One measure per panel. RSRP, RSRQ and SINR have different scales, and a shared axis would misrepresent all three."),
    ("RSRQ", "rackphone_lte_rsrq_db", "rackphone_nr_ss_rsrq_db", "dB", None),
    ("SINR", "rackphone_lte_sinr_db", "rackphone_nr_ss_sinr_db", "dB", None),
]:
    L.add(title, "timeseries", 8, 7, [
            target(f"{lte}{U}", "LTE slot {{slot}}", "A"),
            target(f"{nr}{U}", "NR slot {{slot}}", "B")],
          fc(unit=unit_, dec=0, custom=LINE, nodata="no reading"), TS, desc)
L.add("Registration", "state-timeline", 12, 5, [
        target(f"rackphone_data_registered{U}", "data slot {{slot}}", "A"),
        target(f"rackphone_voice_registered{U}", "voice slot {{slot}}", "B")],
      fc(color=THRESH,
         mappings=[{"type": "value", "options": {"0": {"color": "red", "index": 1, "text": "out"},
                                                 "1": {"color": "green", "index": 0, "text": "in service"}}}],
         thresholds=steps(("red", None), ("green", 1))), TIMELINE)
L.add("Scrape cost", "timeseries", 12, 5, [
        target(f"rackphone_scrape_duration_seconds{U}", "on-device", "A"),
        target(f"rackphone_collect_duration_seconds{U}", "host round-trip", "B")],
      fc(unit="s", dec=3, mn=0, custom=LINE), TS,
      "If the host round-trip climbs toward the 10s scrape timeout, the USB link is degrading.")

unit_dash = dashboard("rackphone-unit", "Rackphone / Unit",
                      "One device in detail. Pick a unit above.", L.panels,
                      variables=[unit_var(multi=False, all_=False)], links=NAV)

# ===========================================================================
# 3. BATTERY - ageing over weeks, not hours
# ===========================================================================
L = Layout()
L.row("Pack condition")
L.add("Health by unit", "bargauge", 8, 6,
      [target("rackphone_battery_health_ratio * 100", "{{unit}}", "A", instant=True)],
      fc(unit="percent", dec=1, mn=0, mx=100, color=THRESH,
         thresholds=steps(("red", None), ("orange", 70), ("yellow", 85), ("green", 92))),
      {"displayMode": "gradient", "orientation": "horizontal", "showUnfilled": True,
       "minVizWidth": 8, "minVizHeight": 16, "valueMode": "color", "sizing": "auto",
       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
      "Measured capacity against design capacity.")
L.add("Cycles by unit", "bargauge", 8, 6,
      [target("rackphone_battery_cycle_count", "{{unit}}", "A", instant=True)],
      fc(dec=0, mn=0, color=THRESH,
         thresholds=steps(("green", None), ("yellow", 500), ("orange", 800), ("red", 1200))),
      {"displayMode": "gradient", "orientation": "horizontal", "showUnfilled": True,
       "minVizWidth": 8, "minVizHeight": 16, "valueMode": "color", "sizing": "auto",
       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
      "Li-ion is typically specified to ~500 full cycles before noticeable fade.")
L.add("Capacity, measured vs design", "bargauge", 8, 6, [
        target("rackphone_battery_charge_full_ampere_hours * 1000", "{{unit}} now", "A", instant=True),
        target("rackphone_battery_charge_design_ampere_hours * 1000", "{{unit}} design", "B", instant=True)],
      fc(unit="mAh", dec=0, mn=0, color=BY_NAME),
      {"displayMode": "basic", "orientation": "horizontal", "showUnfilled": True,
       "minVizWidth": 8, "minVizHeight": 16, "valueMode": "text", "sizing": "auto",
       "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}})

L.row("Trends")
L.add("Health over time", "timeseries", 12, 8,
      [target("rackphone_battery_health_ratio * 100", "{{unit}}", "A")],
      fc(unit="percent", dec=2, custom=LINE, color=BY_NAME), TS_TABLE,
      "Only meaningful over weeks. The fuel gauge re-estimates capacity slowly and in steps.")
L.add("Cycle count over time", "timeseries", 12, 8,
      [target("rackphone_battery_cycle_count", "{{unit}}", "A")],
      fc(dec=0, custom=LINE, color=BY_NAME), TS_TABLE,
      "Slope is the real number to watch: a wider charge window should flatten it.")
L.add("Charge level", "timeseries", 12, 8,
      [target("rackphone_battery_capacity_percent", "{{unit}}", "A")],
      fc(unit="percent", mn=0, mx=100, dec=0, custom=AREA, color=BY_NAME), TS,
      "Over a long window this shows whether the guard is actually holding the window.")
L.add("Time spent suspended", "timeseries", 12, 8,
      [target("avg_over_time(rackphone_battery_charging_suspended[1h]) * 100", "{{unit}}", "A")],
      fc(unit="percent", mn=0, mx=100, dec=0, custom=AREA, color=BY_NAME), TS,
      "Fraction of each hour with charging held off. Near 100% means the pack sits at the ceiling.")
L.add("Battery temperature", "timeseries", 24, 7,
      [target("rackphone_battery_temperature_celsius", "{{unit}}", "A")],
      fc(unit="celsius", dec=1, custom=LINE, color=BY_NAME), TS_TABLE,
      "Heat ages a pack faster than cycling does. Sustained time above ~35C is the thing to fix.")

battery = dashboard("rackphone-battery", "Rackphone / Battery",
                    "Pack ageing across the fleet. Use a 30d or 90d range.",
                    L.panels, variables=[], time_from="now-30d",
                    refresh="5m", links=NAV)

# ===========================================================================
# 4. MESSAGING - will the SIMs survive, and is anything stuck?
# ===========================================================================
# A separate dashboard because it answers a question with a different horizon.
# Battery ages over weeks and thermals move in minutes; a SIM dies over months,
# quietly, and the panels that catch it are the ones nobody looks at daily.
L = Layout()
L.row("SIM health")

L.add("Balance", "stat", 8, 5,
      [target("rackphone_companion_balance", "{{unit}} sub {{sub_id}}", "A", instant=True)],
      fc(unit="currencyRUB", dec=2, color=THRESH,
         thresholds=steps(("red", None), ("orange", 30), ("green", 100))),
      stat(), "Red below 30 is not a warning but an outage in waiting: a "
              "keepalive SMS costs a few roubles, and a SIM that cannot send "
              "one is a SIM the operator reclaims.")

L.add("Balance last read", "stat", 8, 5,
      [target("rackphone_companion_balance_age_seconds", "{{unit}} sub {{sub_id}}",
              "A", instant=True)],
      fc(unit="s", dec=0, color=THRESH,
         thresholds=steps(("green", None), ("orange", 172800), ("red", 259200))),
      stat(), "A balance that stopped being refreshed is not a balance. This "
              "is what keeps the panel to its left honest.")

L.add("Alarms will run", "stat", 8, 5,
      [target("rackphone_companion_battery_exempt", "{{unit}}", "A", instant=True)],
      fc(dec=0, color=THRESH, mappings=YESNO,
         thresholds=steps(("red", None), ("green", 1))),
      stat(), "Android defers an unopened app's alarms by up to a year. When "
              "this is no, the keepalive below is a schedule nobody executes.")

L.add("Next keepalive", "stat", 12, 5,
      [target("rackphone_companion_keepalive_seconds_until_due", "{{unit}}", "A",
              instant=True)],
      fc(unit="s", dec=0, color=THRESH,
         thresholds=steps(("green", None), ("text", 1))),
      stat(mode="value"),
      "Time until the earliest SIM is due. Any successful send resets it, so "
      "a busy unit shows a full interval and sends nothing extra.")

L.add("Targets that resolve to nothing", "stat", 12, 5,
      [target('sum by (unit) (rackphone_companion_keepalive_targets{resolves="false"})',
              "{{unit}}", "A", instant=True)],
      fc(dec=0, color=THRESH,
         thresholds=steps(("green", None), ("red", 1))),
      stat(), "A SIM whose own number is not on the card cannot text itself. "
              "Everything else about it looks healthy, which is why it is here.")

L.row("Balance over time")
L.add("Balance", "timeseries", 24, 9,
      [target("rackphone_companion_balance", "{{unit}} sub {{sub_id}}", "A")],
      fc(unit="currencyRUB", dec=2, custom=AREA, color=BY_NAME), TS_TABLE,
      "Steps down are sends; a flat line at a low value is a SIM about to stop "
      "being able to keep itself alive. Read over 30d.")

L.row("Delivery")
L.add("Waiting for the host", "timeseries", 12, 7,
      [target("rackphone_companion_events_pending", "{{unit}}", "A")],
      fc(dec=0, custom=AREA, color=BY_NAME), TS,
      "Events collected and not yet acked. A rising line means the gateway "
      "stopped draining, not that the phone is busy.")

L.add("Dropped at the cap", "timeseries", 12, 7,
      [target("increase(rackphone_companion_events_dropped_total[1h])", "{{unit}}", "A")],
      fc(dec=0, custom=AREA, color=FIXED("red")), TS,
      "Anything above zero is a message that existed and no longer does.")

L.add("Sends, by outcome", "timeseries", 12, 7,
      [target("increase(rackphone_companion_sent_total[24h])",
              "{{unit}} {{outcome}}", "A")],
      fc(dec=0, custom=LINE, color=BY_NAME), TS_TABLE,
      "Rejected means it never reached the radio - permission, no SIM, an "
      "unusable number. Failed means the radio took it and the network did not.")

L.add("Companion", "stat", 12, 7,
      [target("rackphone_companion_up", "{{unit}}", "A", instant=True)],
      fc(dec=0, color=THRESH, mappings=OKDEAD,
         thresholds=steps(("red", None), ("green", 1))),
      stat(), "The app installed, permitted and holding a SIM. Everything else "
              "on this dashboard is meaningless while this is DEAD.")

messaging = dashboard("rackphone-messaging", "Rackphone / Messaging",
                      "SIM survival and the relay: balance, keepalive, spool.",
                      L.panels, variables=[unit_var()], time_from="now-30d",
                      refresh="1m", links=NAV)

for name, d in [("fleet", fleet), ("unit", unit_dash), ("battery", battery),
                ("messaging", messaging)]:
    path = OUT / f"rackphone-{name}.json"
    path.write_text(json.dumps(d, indent=2) + "\n")
    n = len([p for p in d["panels"] if p["type"] != "row"])
    r = len([p for p in d["panels"] if p["type"] == "row"])
    print(f"{path.name:<28} {n:>2} panels, {r} rows")
