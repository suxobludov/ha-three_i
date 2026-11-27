**Автор интеграции 3i Robot Vacuum (ha-three_i) — suxobludov.** Эти материалы — дополнение к его интеграции.

# Карта и режимы уборки 3i P10 Ultra — материалы исследования

Логи и разборы, по которым сделаны карта помещения, кнопки комнат и режимы уборки
комнаты. Всё снято 27.11.2026 на 3i P10 Ultra (прошивка P4.6.158_49, аккаунт из РФ,
кластер cn), Home Assistant 2026.11.2.

**Из логов убраны:** серийный номер, MAC, IP и SSID, userId. Если эти файлы в
репозитории не нужны — папку можно просто удалить, коду она не нужна.

| Файл | Что внутри |
|---|---|
| `mqtt-1-map-services.log` | запросы из HA `get_map_list`, `get_room_list`, `get_map`, `get_map_info`, `upload_by_maptype` и ответы робота |
| `mqtt-2-room-clean-app.log` | приложение 3i: 1) сухая уборка, мощность «тихий»; 2) сухая и влажная, уборка комнаты 10. Видны `set_preference_type`, `prop.set` (`section`, `repeat_state`, `wind`), `set_room_clean`, `upload_by_mapid`, `get_preference`, координаты робота `cur_path` |
| `mqtt-3-modes-app.log` | приложение 3i: переключение режимов по очереди — Авто → Сухая и влажная → Сухая → Влажная → По выбору |
| `device-list-sanitized.json` | ответ `getDeviceInfoByUserId` (оставлены только `productId`, `productModeCode`, `productName`, версия прошивки) |
| `map-decoded-summary.json` | разбор файлов карты `_1` и `_2`: поля protobuf, заголовок, гистограмма значений клеток, комнаты, контуры |
| `map-file-3-shortcut-tasks.json` | файл `_3` — JSON сценариев уборки (cmd_type, room_preference) |
| `map-render-example.png` | как интеграция рисует карту (прозрачный фон) |
| `screenshot-dashboard.png`, `screenshot-map-popup.png` | пример дашборда на bubble-card (сам дашборд в PR не входит) |
| `example-room-clean-script.yaml` | пример скрипта HA: уборка комнаты с режимом, мощностью, водой и проходами |

## Коды режимов (из `mqtt-3-modes-app.log`)

| Режим в приложении | `service.set_preference_type` |
|---|---|
| Авто | `{"prefer_type": 3, "mode": 4}` |
| Сухая и влажная | `{"prefer_type": 0, "mode": 1}` |
| Сухая | `{"prefer_type": 0, "mode": 0}` |
| Влажная | `{"prefer_type": 0, "mode": 2}` |
| По выбору (настройки по комнатам) | `{"prefer_type": 11}` |

После смены режима приложение всегда шлёт `prop.set {"section": N}`.
Что такое `section` и `repeat_state`, точно не установлено (предположительно проходы).
