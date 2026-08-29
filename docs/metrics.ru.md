# Метрики

Измерено на первом юните: **211 рядов** за скрейп в **0,44 с**, при включённом
`telemetry.collect_telephony` и тепловом фильтре по умолчанию.

Каждый сэмпл получает от моста метку `unit`, потому что один процесс отдаёт
несколько телефонов, а собственный `instance` в Prometheus их не различает.

> Русский перевод [metrics.md](./metrics.md). Исходная английская версия — основная.

## Сентинелы недоступности

Недоступное показание радио Android сообщает как `Integer.MAX_VALUE`
(`2147483647`). Такие сэмплы **опускаются**, а не экспортируются. Отсутствующий
ряд означает «нет показания»; экспорт сентинела поставил бы на каждую панель
всплески на 2,1 миллиарда и отравил бы любой `avg` или `max` по диапазону.

На тестовом юните второй слот SIM пуст — поэтому у `slot="1"` есть метрики
регистрации, но нет ни одной метрики сигнала.

## Батарея

| Метрика | Источник | Root |
| --- | --- | --- |
| `rackphone_battery_capacity_percent` | `dumpsys battery` | нет |
| `rackphone_battery_voltage_volts` | `dumpsys battery` | нет |
| `rackphone_battery_temperature_celsius` | `dumpsys battery` | нет |
| `rackphone_battery_charge_counter_ampere_hours` | `dumpsys battery` | нет |
| `rackphone_battery_charge_full_ampere_hours` | `dumpsys battery` | нет |
| `rackphone_battery_charge_design_ampere_hours` | `dumpsys battery` | нет |
| `rackphone_battery_health_ratio` | full ÷ design | нет |
| `rackphone_battery_charging` | статус `2` | нет |
| `rackphone_battery_status_code` | 2 заряжается, 3 разряжается, 4 не заряжается, 5 полная | нет |
| `rackphone_power_supply_online{supply}` | `ac`, `usb`, `wireless` | нет |
| `rackphone_battery_current_amperes` | `battery/current_now` | да |
| `rackphone_battery_cycle_count` | `battery/cycle_count` | да |
| `rackphone_battery_soh_percent` | `qcom-battery/soh` | да |
| `rackphone_battery_internal_resistance_ohms` | `qcom-battery/resistance` | да |
| `rackphone_connector_temperature_celsius` | `qcom-battery/connector_temp` | да |

**Два числа SOH, и они не сходятся.** `rackphone_battery_health_ratio` — это
`charge_full / charge_full_design`, и на тестовом юните он дал **0,7388**;
`rackphone_battery_soh_percent` приходит от вендорского топливомера и дал **81**.
Они измеряют разное, и ни одно не ошибочно — стройте график отношения ради
тренда, а вендорскую цифру считайте личным мнением топливомера. За этой батареей
**1385 циклов**, и это честное объяснение обоих чисел.

Вендорские ноды `fg1_*` на этом ядре есть, но читаются как `0`, поэтому такие
ряды присутствуют и бесполезны. Их оставили, потому что на родственном
устройстве они могут заполняться.

## Guard заряда

| Метрика | Значение |
| --- | --- |
| `rackphone_battery_guard_up` | Цикл жив. **Алертить именно по ней.** |
| `rackphone_battery_charging_suspended` | Guard удерживает зарядку выключенной |
| `rackphone_battery_window_percent{bound}` | `min`, `max`, `floor` |
| `rackphone_battery_control_method_info{node}` | Какая нода sysfs используется |

`rackphone_battery_guard_up == 0` при `rackphone_battery_charging_suspended == 1`
— то состояние, ради которого стоит будить дежурного: оно означает, что цикл
умер посреди приостановки. Guard изо всех сил старается сделать это
невозможным — он перехватывает каждый путь выхода, — но kill, который он поймать
не может, оставил бы юнит молча разряжаться.

## Температуры

`rackphone_temperature_celsius{zone}` — **15 зон из 89** при фильтре по
умолчанию. Расширяйте `telemetry.thermal_include` осознанно: исключённые зоны —
в основном датчики отдельных ядер и mmWave, причём mmWave-зоны на устройстве без
mmWave-железа читаются константой `2000` (2 °C).

## CPU, память, хранилище

| Метрика | Примечания |
| --- | --- |
| `rackphone_cpu_frequency_hertz{cpu}` | Только для активных ядер |
| `rackphone_cpu_online{cpu}` | Android агрессивно паркует ядра |
| `rackphone_cpu_seconds_total{mode}` | Счётчик; используйте `rate()` |
| `rackphone_load1` / `_load5` / `_load15` | |
| `rackphone_memory_bytes{kind}` | `total`, `free`, `available`, `cached`, `buffers`, `swap_*` |
| `rackphone_filesystem_bytes{mount,kind}` | Только `/data` |
| `rackphone_disk_bytes_total{device,op}` | Только устройства целиком |
| `rackphone_uptime_seconds` | |

## Сеть

`rackphone_network_bytes_total{interface,direction}`, а также `_packets_total` и
`_errors_total`. Это счётчики — считайте пропускную способность в запросе, а не
храните готовую скорость:

```promql
rate(rackphone_network_bytes_total{interface="rmnet_data4",direction="rx"}[1m])
```

## Радио

| Метрика | Показание тестового юнита |
| --- | --- |
| `rackphone_lte_rsrp_dbm{slot}` | −98 |
| `rackphone_lte_rsrq_db{slot}` | −13 |
| `rackphone_lte_rssi_dbm{slot}` | −73 |
| `rackphone_lte_sinr_db{slot}` | 8 |
| `rackphone_lte_timing_advance{slot}` | недоступно |
| `rackphone_nr_ss_rsrp_dbm{slot}` и т.д. | 5G NR, когда зарегистрирован |
| `rackphone_voice_registered{slot}` / `_data_registered{slot}` | 1 / 1 |
| `rackphone_radio_channel_number{slot}` | 3648 (EARFCN) |
| `rackphone_radio_info{slot,rat,operator}` | `LTE`, `beeline` |

Разбирается из `dumpsys telephony.registry`, формат которого **не является
стабильным API**. Обновление LineageOS может сдвинуть эти поля; после него
перезапустите `scripts/inventory.sh` и проверьте, что секция радио всё ещё
парсится.

## Мета

| Метрика | Значение |
| --- | --- |
| `rackphone_up{unit}` | Юнит ответил на этот скрейп (добавляется мостом) |
| `rackphone_collect_duration_seconds{unit}` | Круг на стороне хоста |
| `rackphone_scrape_duration_seconds` | Время сбора на устройстве |
| `rackphone_root_available` | Привилегированные чтения проходят |
| `rackphone_plugins_installed` | Число включённых плагинов |

## Не экспортируется намеренно

IMSI, ICCID, IMEI, номера телефонов, тексты SMS и координаты GPS. Часть из
этого — проблемы приватности; всё это — метки неограниченной кардинальности,
которые постепенно замедляли бы базу временных рядов ради данных, по которым она
всё равно не может делать полезные запросы. *Счётчики* звонков и SMS как метрики
допустимы; содержимому место в настоящей базе данных.
