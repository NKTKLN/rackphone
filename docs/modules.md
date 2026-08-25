# Plugin contract

A Magisk module becomes a Rackphone plugin by shipping a `rackphone/` directory.
Nothing else registers it: `rackphone-core` discovers plugins by scanning
`/data/adb/modules/*/rackphone/plugin.json` at every invocation, and honours
Magisk's own `disable` marker so a module turned off in Magisk disappears from
the CLI too.

The plugin id is the module id with `rackphone-` stripped, which is why settings
read `battery.max_percent` rather than repeating the namespace.

## Files

| File | Required | Contract |
| --- | --- | --- |
| `rackphone/plugin.json` | yes | Declaration: settings, actions, status keys |
| `rackphone/defaults.env` | yes | `key=value`, generated from `plugin.json` |
| `rackphone/metrics.sh` | no | Prometheus exposition on stdout |
| `rackphone/status.sh` | no | `key=value` lines on stdout |
| `rackphone/action.sh` | no | Called as `action.sh <action-id>` |
| `rackphone/reload.sh` | no | Called after `rackphone set` touches this plugin |

`defaults.env` is generated rather than hand-written. The CLI validates against
`plugin.json`, so a default that disagreed with the declaration would be a bug
visible only on a device with no config deployed.

## Declaration

```json
{
  "id": "battery",
  "name": "Battery Guard",
  "description": "Keeps the pack in a charge window.",
  "settings": [
    {
      "key": "max_percent",
      "type": "int",
      "default": "80",
      "min": 30,
      "max": 100,
      "label": "Stop charging at",
      "unit": "%",
      "help": "Why this value, not what it is."
    }
  ],
  "actions": [ { "id": "resume", "label": "Resume charging now" } ],
  "status": ["guard", "capacity", "suspended"]
}
```

| Field | Types | Notes |
| --- | --- | --- |
| `type` | `bool`, `int`, `enum`, `string` | Drives both validation and rendering |
| `min` / `max` | `int` only | Enforced host-side before anything is written |
| `values` | `enum` only | The permitted set |
| `unit` | any | Suffix shown in `rackphone config` |
| `help` | any | Shown when validation rejects a value |
| `depends_on` | any | Another key that gates this one |

`status` lists the keys `status.sh` may emit, in the order the CLI shows them.

## Resolution

Reading a setting inside a plugin means checking three places in order:

```sh
value=$(getprop "persist.rackphone.$PLUGIN.$key")
[ -z "$value" ] && value=$(sed -n "s/^[[:space:]]*$PLUGIN\.$key=//p" /data/adb/rackphone/config.env | tail -1)
[ -z "$value" ] && value=$(sed -n "s/^[[:space:]]*$key=//p" "$MODDIR/rackphone/defaults.env" | tail -1)
```

Both shipped plugins wrap this as `cfg()` / `rp_cfg()`. Read settings at the top
of each loop iteration rather than once at start: that is what makes a change
take effect within one poll interval without a `reload.sh`.

## Writing metrics.sh

Three rules, each learned from this hardware:

**Drop unavailable readings.** Android encodes "no value" as `Integer.MAX_VALUE`
(`2147483647`). Exporting it puts 2.1-billion spikes on every panel. An absent
series is honest; `MAX_VALUE` is not.

**One awk pass per source.** The device has 89 thermal zones and 8 cores. A `cat`
per attribute is ~200 forks per scrape. Read many files from a single awk with
`getline`.

**Put anything expensive behind a setting.** `dumpsys` costs roughly 150 ms, which
is most of a scrape budget at a 10-second interval.

Emit `# HELP` and `# TYPE` for metrics your plugin owns, and only those — core
concatenates plugin output verbatim, so two plugins emitting the same family
would produce a duplicate that Prometheus rejects.

## Guarding against your own failure

If a plugin can leave the device in a state that needs undoing, undo it on every
exit path. The battery guard traps `INT TERM EXIT` to resume charging, resumes
once at start in case a previous run was killed mid-suspend, and force-resumes
below a safety floor regardless of configuration. It also tries *every* candidate
control node when resuming, not just the selected one, because a stale cached
method must never be the reason a unit cannot charge.
