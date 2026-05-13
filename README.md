# MotionDetectionBundle: сборка и запуск на Raspberry Pi 4B

Этот проект разворачивается на Raspberry Pi **как набор исходников + virtualenv + systemd**, без упаковки в один бинарник.

## 1) Подготовить bundle на машине разработки

```bash
cd MotionDetectionBundle
./scripts/build_rpi_bundle.sh
```

После этого появится архив `dist/motion-detection-rpi4.tar.gz`.

## 2) Скопировать bundle на Raspberry Pi

```bash
scp dist/motion-detection-rpi4.tar.gz <user>@<raspberry-ip>:/tmp/
```

## 3) Установить и запустить одним скриптом

```bash
ssh <user>@<raspberry-ip> "curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/<branch>/MotionDetectionBundle/deploy/install_rpi.sh | sudo bash -s -- /tmp/motion-detection-rpi4.tar.gz"
```

Если скрипт уже лежит на Raspberry Pi, можно запустить так:

```bash
sudo ./install_rpi.sh /tmp/motion-detection-rpi4.tar.gz
```

Скрипт:
- при необходимости сам распакует bundle в `/opt/motion-detection`;
- ставит системные пакеты (`python3-opencv` и т.д.);
- создаёт virtualenv в `/opt/motion-detection/.venv` с доступом к системному OpenCV;
- ставит Python-зависимости из `requirements-rpi.txt`;
- создаёт standalone-команду `/usr/local/bin/motion-detection`;
- выносит конфиг в `/etc/motion-detection/config.json`;
- автоматически определяет пользователя для systemd (без жёсткой привязки к `pi`);
- регистрирует `motion-detection.service`, запускает его сразу и включает автозапуск при старте Raspberry Pi.

## 4) Управление сервисом

```bash
sudo systemctl status motion-detection.service
sudo systemctl restart motion-detection.service
sudo journalctl -u motion-detection.service -f
```

## Важно

- OpenCV на Raspberry Pi берётся из `apt` (`python3-opencv`) для лучшей совместимости ARM.
- Сервис запускается в `--debug` режиме, поэтому веб-интерфейс доступен постоянно на порту `5000` (например, `http://<raspberry-ip>:5000`).

## 5) Команды запуска и режимы работы

После установки можно запускать приложение без исходников репозитория через команду `motion-detection`. Команда всегда использует конфиг `/etc/motion-detection/config.json` и запускает `/opt/motion-detection/app.py` через установленный virtualenv.

### Быстрый выбор режима

| Что нужно сделать | Команда |
| --- | --- |
| Запустить обычный консольный режим | `motion-detection` |
| Запустить веб-интерфейс с видео и отладкой | `motion-detection --debug` |
| Запустить режим настройки камер | `motion-detection --setup` |
| Запустить тест GPIO/событий в консоли | `motion-detection --hwtest` |
| Запустить Debug + тестовые API для GPIO/событий | `motion-detection --debug --hwtest` |
| Применить переменную окружения на один запуск | `motion-detection --debug --override CAMERA_1_RTSP_PASSWORD=secret` |
| Использовать другой конфиг | `motion-detection --config /path/to/config.json --debug` |
| Мигрировать конфиг и выйти | `motion-detection --cfgmigrate` |
| Обновить git-установку | `motion-detection --update` |
| Обновить bundle-установку из архива | `motion-detection --update /tmp/motion-detection-rpi4.tar.gz` |

### Обычный консольный режим

```bash
motion-detection
```

Используйте этот режим, если нужен локальный экран состояния в терминале без веб-видео. В консоли доступны обзор камер, состояние GPIO, последние события и логи.

Горячие клавиши:
- `TAB` — переключение между обзором и выбранной камерой;
- `←` / `→` — выбор камеры;
- `P` — открыть или закрыть панель настроек;
- `Q` — выход.

### Debug-режим с веб-интерфейсом

```bash
motion-detection --debug
```

После запуска откройте в браузере:

```text
http://<raspberry-ip>:5000
```

В этом режиме доступны видеопоток, диагностические режимы `DEBUG`, `RAW`, `MASK`, `THRESH`, статус детекции, логи, настройки камеры и настройка deadzone-зон.

### Setup-режим для добавления и удаления камер

```bash
motion-detection --setup
```

После запуска откройте:

```text
http://<raspberry-ip>:5000
```

В Setup-режиме можно добавлять и удалять камеры. Видеопоток в этом режиме отключён; для просмотра картинки и настройки deadzone используйте `motion-detection --debug`.

### Hardware test-режим

```bash
motion-detection --hwtest
```

Этот режим добавляет в консоль вкладку тестирования. Он нужен для проверки GPIO и логики событий без сохранения временных изменений в `config.json`.

Горячие клавиши тестового режима:
- `M` — открыть вкладку Test;
- `T` — включить или выключить Test mode;
- `A` — включить или выключить авто-детекцию, доступно только при включённом Test mode;
- `G` — вручную переключить GPIO `HIGH`/`LOW`, доступно только при включённом Test mode;
- `E` — вручную выставить или снять событие, доступно только при включённом Test mode.

Если нужен веб-интерфейс и тестовые API одновременно, запустите:

```bash
motion-detection --debug --hwtest
```

### Запуск с временными переменными окружения

```bash
motion-detection --debug --override CAMERA_1_RTSP_PASSWORD=secret
```

Так удобно передавать пароль камеры, если RTSP URL в конфиге содержит переменную вида `${CAMERA_1_RTSP_PASSWORD}`. Значение применяется только для текущего запуска процесса.

### Запуск с другим конфигом

```bash
motion-detection --config /path/to/config.json --debug
```

Эта команда полезна для проверки отдельного тестового конфига без изменения основного `/etc/motion-detection/config.json`.

### Миграция конфига

```bash
motion-detection --cfgmigrate
```

`--cfgmigrate` выполняет миграцию `/etc/motion-detection/config.json` к актуальному формату (multi-camera + новые поля) и завершает работу без запуска сервиса.

`--update` выполняет безопасное обновление:
- если `/opt/motion-detection` — git-репозиторий, выполняется `git fetch --all --prune` + `git pull --ff-only origin master`;
- если это bundle-установка без `.git`, передайте путь к tar.gz: `motion-detection --update /path/to/motion-detection-rpi4.tar.gz`;
- если `motion-detection.service` запущен, он будет остановлен на время обновления и запущен обратно;
- при наличии обновляются Python-зависимости из `requirements-rpi.txt`.
- после обновления автоматически запускается миграция конфига (эквивалент `--cfgmigrate`).

События обновления логируются через `logger` (tag: `motion-detection-update`) и доступны в journald:

```bash
sudo journalctl -t motion-detection-update -f
```

`motion-detection` запускает `/opt/motion-detection/app.py` через установленный venv и по умолчанию использует конфиг из `/etc/motion-detection/config.json`.

> Защита от дублей: одновременно разрешён только один экземпляр процесса (через lock-файл). Если сервис уже запущен, повторный `motion-detection` не создаст второй процесс — вместо этого используйте веб-интерфейс `http://<raspberry-ip>:5000`.

### Параметр против «зацикливания света»

Если прожектор включается по детекции и сам создаёт резкое изменение кадра, используйте параметр:

- `light_settle_seconds` — сколько секунд игнорировать авто-детекцию после переключения GPIO (и при включении, и при выключении), по умолчанию `4.0`.
- `max_detected_objects` — максимальное количество одновременно обнаруженных объектов (контуров выше `min_area`). `0` = без ограничения.
- `max_motion_fill_ratio` — фильтр ложных срабатываний от резких вспышек/глобальных изменений кадра. Если доля «движущихся» пикселей выше порога, событие игнорируется. По умолчанию `0.6`.
