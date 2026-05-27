Вступление RU.

{% list tabs %}

- mirror-3-dc-3nodes

  ```yaml
  services_enabled:
  - legacy
  ```

{% endlist %}

## Запустите узлы {#start}

{% list tabs group=manual-systemd %}

- Вручную

  ```bash
  sudo su - ydb
  ```

- С использованием systemd

  Образец можно [скачать из репозитория](https://example.com/ydbd-storage.service).

  ```bash
  sudo systemctl start ydbd-storage
  ```

{% endlist %}

Заключение RU.
