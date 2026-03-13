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

## 5) Ручной запуск той же логики, что в `app.py`

После установки можно запускать приложение без исходников репозитория:

```bash
motion-detection
motion-detection --debug
motion-detection --setup
```

Эта команда запускает `/opt/motion-detection/app.py` через установленный venv и всегда использует конфиг из `/etc/motion-detection/config.json`.

> Защита от дублей: одновременно разрешён только один экземпляр процесса (через lock-файл). Если сервис уже запущен, повторный `motion-detection` не создаст второй процесс — вместо этого используйте веб-интерфейс `http://<raspberry-ip>:5000`.
