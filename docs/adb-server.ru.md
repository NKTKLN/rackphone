# ADB-сервер на хостовой виртуалке

Бридж работает в Docker, а телефоны висят на USB, поэтому кто-то должен
перенести `adb` через эту границу. Это хостовая половина: adb-сервер как
системный сервис, слушающий сокет, до которого дотягивается контейнер.

> Русский перевод [adb-server.md](./adb-server.md). Исходная английская версия — основная.

> [!WARNING]
> **У adb-сервера нет аутентификации.** Кто может открыть его порт, тот
> полностью управляет всеми подключёнными телефонами — install, shell, pull.
> Привязывайте его к docker-мосту и ни к чему больше и никогда не выставляйте
> 5037 в локальную сеть. Если контейнеры на другой машине — заворачивайте в
> туннель (WireGuard или `ssh -L`), а не открывайте порт.

## 📦 Пакеты

| Дистрибутив | Установка |
| --- | --- |
| Debian / Ubuntu | `apt install adb android-sdk-platform-tools-common` |
| Fedora / RHEL | `dnf install android-tools` |
| Arch | `pacman -S android-tools` |

`android-sdk-platform-tools-common` в Debian кладёт
`/lib/udev/rules.d/51-android.rules` с большинством вендоров. Правила ниже всё
равно нужны: они добавляют группу, под которой работает сервис, и выключают
USB autosuspend.

Больше ничего не требуется: у образа бриджа свой Python, а CLI на хосте не
запускается вовсе.

## 🔌 Как телефон попадает в виртуалку

На гипервизоре пробрасывайте USB **по порту**, а не по vendor:product:

```sh
lsusb -t                        # найти шину и порт, например 1-4
qm set 100 -usb0 host=1-4       # Proxmox, VM 100
```

vendor:product меняется, когда телефон уходит в fastboot или recovery, и
совпадает у двух одинаковых телефонов. Порт переживает и то и другое, а заодно
превращает «телефон в третьем слоте» в физический факт, а не в поиск по списку.

Проверить, что доехало, уже внутри виртуалки:

```sh
lsusb | grep 18d1
```

Искать по имени производителя бесполезно, и это сбивает с толку: LineageOS
оставляет дефолтные AOSP-овские id гаджета, поэтому Xiaomi 11 Lite 5G NE
представляется как `18d1:4e11 Google Inc. Nexus One`. Собственный id вендора
(`2717` у Xiaomi) возвращается только на стоковой прошивке.

`Driver=usbfs` в выводе `lsusb -t` означает, что устройство уже кем-то открыто —
обычно adb-сервером на самом гипервизоре. Его надо остановить до проброса,
иначе они будут драться за телефон:

```sh
pgrep -a adb && adb kill-server
```

## 👤 Учётка сервиса и права на USB

Сервер работает под своим пользователем, а udev отдаёт этому пользователю
устройство:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin adb
sudo install -m 0644 scripts/51-rackphone-adb.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Vendor id телефона, которого нет в правилах, показывает `lsusb`, пока он
воткнут.

## ⚙️ Сервис

```sh
sudo install -m 0644 scripts/adb-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adb-server
systemctl status adb-server
```

Три вещи в этом юните несущие:

**`ExecStart` слушает localhost.** Не docker-адрес: adb его отвергает, и сервис
падает с `listening on specified hostname currently unsupported`. Как до него
дотянуться из контейнера — в следующем разделе.

**`Environment=HOME=%S/rackphone-adb`.** RSA-ключ, который авторизует телефон,
лежит в `$HOME/.android/adbkey`. Если он пересоздастся — потому что HOME был
временным или сервис запустился под другим пользователем, — телефон снова
покажет *Разрешить отладку по USB?*, а на юните в стойке некому нажать.
`StateDirectory=` сохраняет каталог через перезагрузки и обновления пакетов.

**`PrivateDevices=` отсутствует намеренно.** Это первое, что добавляют при
харденинге юнита, оно прячет `/dev/bus/usb`, и сервер после этого не видит ни
одного устройства — без внятной ошибки.

**В `RestrictAddressFamilies=` есть `AF_NETLINK`.** libusb узнаёт о воткнутом
телефоне из netlink-сокета. Без него первый скан работает, а hotplug больше
никогда не срабатывает — выглядит ровно как плохой кабель.

## 🌉 Как контейнер попадает внутрь

**adb не умеет слушать на произвольном адресе.** Сервер принимает только
`tcp:<порт>` — все интерфейсы, это делает `-a` — или `tcp:localhost:<порт>`.
На чём угодно другом он падает при старте:

```text
F adb : main.cpp:165 could not install *smartsocket* listener:
        listening on specified hostname currently unsupported
```

Поэтому «привязать к docker-мосту» — не вариант, как бы разумно это ни звучало.
Сервис слушает localhost, а контейнер добирается до него одним из трёх способов.

### Host networking — по умолчанию и предпочтительно

```yaml
services:
  bridge:
    network_mode: host
    environment:
      ADB_SERVER_SOCKET: tcp:127.0.0.1:5037
```

Контейнер разделяет сетевое пространство хоста, поэтому его `localhost` — это
localhost хоста, и порт adb не выставлен вообще никуда: ни правил файрвола, в
которых можно ошибиться, ни адреса, который может уехать. Цена — порты
контейнера садятся прямо на хост (`:9105` у бриджа), а `extra_hosts` и `ports:`
перестают действовать.

### Прокси сокета, если контейнер должен остаться в bridge-сети

systemd пробрасывает адрес моста на localhost, а adb остаётся приватным:

```sh
sudo install -m 0644 scripts/adb-proxy.socket scripts/adb-proxy.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now adb-proxy.socket
```

Включается только сокет: сервис поднимает он сам, при первом подключении. Оба
файла лежат в `scripts/`, и адрес, который занимает сокет, — единственное, что
нужно менять, если у вашего моста не `172.17.0.1`.

В Debian бинарник лежит в `/lib/systemd/systemd-socket-proxyd`. Контейнер при
этом сохраняет `ADB_SERVER_SOCKET: tcp:host.docker.internal:5037` и запись в
`extra_hosts`, как в отслеживаемом compose-файле.

### `-a` и файрвол перед ним

Флаг, к которому тянется каждая инструкция. Он слушает на **всех** интерфейсах,
включая локальную сеть, поэтому безопасен только вместе с правилами:

```sh
sudo sed -i 's|adb -L tcp:localhost:5037|adb -a -P 5037|' /etc/systemd/system/adb-server.service
sudo iptables -N ADB-GUARD
sudo iptables -A ADB-GUARD -i lo -j RETURN
sudo iptables -A ADB-GUARD -i docker0 -j RETURN
sudo iptables -A ADB-GUARD -j DROP
sudo iptables -I INPUT -p tcp --dport 5037 -j ADB-GUARD
```

Их надо сохранить (`iptables-persistent` или аналог для nftables), иначе после
перезагрузки правил не будет, а порт останется открытым. И проверить с другой
машины, прежде чем верить:

```sh
nmap -p 5037 <lan-ip-хоста>     # closed
```

## 📱 Разовая авторизация телефона

```sh
sudo -u adb env HOME=/var/lib/rackphone-adb adb devices
```

Первый запуск напечатает `unauthorized`, а телефон покажет *Разрешить отладку
по USB?*. Отметьте **Всегда разрешать с этого компьютера** и подтвердите.
Дальше телефон держится на ключе из `/var/lib/rackphone-adb/.android/adbkey`.

На телефоне с root тап можно не делать вовсе — ключ кладётся прямо в список
разрешённых:

```sh
adb push /var/lib/rackphone-adb/.android/adbkey.pub /data/local/tmp/adbkey.pub
adb shell su -c 'cat /data/local/tmp/adbkey.pub >> /data/misc/adb/adb_keys'
adb shell su -c 'chmod 640 /data/misc/adb/adb_keys; chown system:shell /data/misc/adb/adb_keys'
```

Это стоит сделать до того, как юнит уедет в стойку: телефон, вернувшийся из
сброса к заводским, без экрана рядом иначе недостижим.

## 🔑 Хранение ключа

Что-то лежит в трёх местах, и они не взаимозаменяемы:

| Путь | Что это |
| --- | --- |
| `/var/lib/rackphone-adb/.android/adbkey` | Приватный ключ сервиса — секрет |
| `/var/lib/rackphone-adb/.android/adbkey.pub` | Его публичная половина |
| `/data/misc/adb/adb_keys` на телефоне | Доверенные публичные ключи, по одному в строке |

Копию пары держите там, где место секретам, и никогда — в этом репозитории:

```sh
sudo tar -czf /tmp/adb-key.tgz -C /var/lib/rackphone-adb/.android adbkey adbkey.pub
```

Восстанавливать нужно вместе с владельцем и правами. Ключ, который adb не может
прочитать, — это ключ, который adb заменит, и телефон снова покажет диалог:

```sh
sudo install -d -o adb -g adb -m 700 /var/lib/rackphone-adb/.android
sudo install -o adb -g adb -m 600 adbkey     /var/lib/rackphone-adb/.android/adbkey
sudo install -o adb -g adb -m 644 adbkey.pub /var/lib/rackphone-adb/.android/adbkey.pub
sudo systemctl restart adb-server
```

Надёжнее второй экземпляр страховки — список на телефоне. Если публичный ключ уже
в `/data/misc/adb/adb_keys`, потеря приватного стоит пяти минут на генерацию
нового, а не поездки туда, где стоит стойка:

```sh
adb shell su -c 'wc -l /data/misc/adb/adb_keys'
```

## ✅ Проверка

Перезапускать — через systemd и с ожиданием порта, а не через `adb kill-server`:
эта команда убивает тот самый сервер, которым управляет сервис, и любая команда
`adb` в секунду до того, как systemd успел заново занять порт, поднимает
приватный демон, который потом дерётся с настоящим. Симптом —
`protocol fault (couldn't read status)`, а лечит его `ExecStartPre` при
следующем перезапуске.

```sh
sudo systemctl restart adb-server
until ss -ltn | grep -q '127.0.0.1:5037'; do sleep 0.2; done
```

```sh
# На хосте, под любым пользователем:
ADB_SERVER_SOCKET=tcp:127.0.0.1:5037 adb devices -l

# Из контейнера:
docker compose --profile bridged run --rm bridge adb devices -l

# Целиком:
docker compose --profile bridged run --rm bridge rackphone devices
```

Все три должны показать один и тот же серийник. Если хост телефон видит, а
контейнер нет — дело в адресе привязки, а не в adb.

## 🩺 Когда не работает

| Симптом | Причина |
| --- | --- |
| `unauthorized` | Диалог не подтвердили, или сменился HOME, а с ним ключ |
| `no permissions` с подсказкой про udev | Правила не установлены или группы `adb` нет на устройстве |
| `listening on specified hostname currently unsupported` | В `-L` передали адрес. adb принимает `localhost` или `-a`, ничего между |
| Устройства пропадают после часов простоя | USB autosuspend — вторая половина файла правил |
| Контейнер не видит ничего, хост видит всё | Сервер на localhost: забыт `-L` или посторонняя команда `adb` подняла второй |
| Два сервера дерутся | Команда `adb` от другого пользователя поднимает свой на `127.0.0.1:5037` с другим ключом, и телефон снова покажет диалог |

```sh
journalctl -u adb-server -f
```

## 🧭 Когда контейнеры в другом месте

Профиль `bridged` в compose существует ради хоста, у которого телефоны висят на
другой машине. Переносить ради этого adb-сервер не надо: **adb-сервер на
маршрутизируемом адресе — это неаутентифицированный root-шелл на каждом
подключённом телефоне.** Оставьте его на localhost там, где телефоны, и
перенесите сокет по SSH.

Разово, с машины, которой он нужен:

```sh
ssh -N -L 5038:127.0.0.1:5037 phones-host
ADB_SERVER_SOCKET=tcp:127.0.0.1:5038 adb devices -l
```

Порт 5038, а не 5037, потому что локальный adb-сервер, скорее всего, уже держит
5037 — а если не держит, локальный клиент его с удовольствием поднимет, и вы
будете смотреть в пустой список устройств и гадать почему.

> [!WARNING]
> **Версии клиента и сервера должны совпадать.** Клиент, обнаруживший сервер
> другой версии, убивает его и поднимает свой — а через туннель это означает,
> что он убил удалённый сервис, поднять замену там не может, и дальше рестарт
> systemd гоняется со следующей командой. Сверьте `adb version` с обеих сторон.

Как сервис, чтобы пережил перезагрузку и обрыв связи:

```sh
sudo install -m 0644 scripts/adb-tunnel@.service /etc/systemd/system/
sudo systemctl enable --now adb-tunnel@phones-host
```

Имя инстанса — ssh-назначение или алиас `Host` из конфига пользователя `adb`
(`/var/lib/rackphone-adb/.ssh/config`). Контейнеры дальше ходят через тот же
прокси сокета, как будто телефоны локальные.

Ключ для туннеля не должен уметь ничего больше. На хосте с телефонами, в строке
`authorized_keys` этого ключа:

```text
restrict,port-forwarding,permitopen="127.0.0.1:5037" ssh-ed25519 AAAA...
```

`restrict` выключает всё — шелл, агент, X11, любые другие пробросы, — а две
опции возвращают ровно один порт. Ключ, который может дотянуться только до
порта adb, всё равно остаётся ключом, управляющим телефонами, поэтому он
принадлежит служебной учётке, а не человеку.
