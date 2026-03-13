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
scp dist/motion-detection-rpi4.tar.gz pi@<raspberry-ip>:/tmp/
```

На Raspberry Pi:

```bash
sudo mkdir -p /opt/motion-detection
sudo tar -xzf /tmp/motion-detection-rpi4.tar.gz -C /opt
sudo mv /opt/motion-detection-rpi4/* /opt/motion-detection/
```

## 3) Установить зависимости и systemd-сервис

```bash
sudo /opt/motion-detection/deploy/install_rpi.sh
```

Скрипт:
- ставит системные пакеты (`python3-opencv` и т.д.);
- создаёт virtualenv в `/opt/motion-detection/.venv`;
- ставит Python-зависимости из `requirements-rpi.txt`;
- регистрирует и запускает `motion-detection.service`.

## 4) Управление сервисом

```bash
sudo systemctl status motion-detection.service
sudo systemctl restart motion-detection.service
sudo journalctl -u motion-detection.service -f
```

## Важно

- OpenCV на Raspberry Pi берётся из `apt` (`python3-opencv`) для лучшей совместимости ARM.
- Веб-интерфейс доступен на порту `5000` при запуске `app.py --debug`/`--setup`.
